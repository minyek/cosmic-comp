// Cross-process dmabuf import leak test — matches the Wayland client->compositor
// flow: a PRODUCER process allocates GBM buffers and passes their dmabuf fds over
// a unix socket (SCM_RIGHTS); the IMPORTER process imports each as an EGLImage,
// optionally binds + SAMPLES it in a draw, then destroys it. Foreign (cross-
// process) dmabufs are what the driver must map into the importer's GPU address
// space — the case the same-process test could not exercise.
//
// If the importer's nvidia-smi GPU memory climbs with iterations, the driver
// leaks on cross-process import churn. argv: repro_xproc <iters> <draw 0|1> <w> <h>

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>

#include <gbm.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>

#define DRM_FORMAT_ARGB8888 0x34325241

struct meta { int w, h; uint32_t stride; uint64_t modifier; };

static PFNEGLGETPLATFORMDISPLAYEXTPROC eglGetPlatformDisplayEXT;
static PFNEGLCREATEIMAGEKHRPROC eglCreateImageKHR;
static PFNEGLDESTROYIMAGEKHRPROC eglDestroyImageKHR;
static PFNGLEGLIMAGETARGETTEXTURE2DOESPROC glEGLImageTargetTexture2DOES;

static int send_fd(int sock, int fd, struct meta *m) {
    struct iovec io = { .iov_base = m, .iov_len = sizeof *m };
    char buf[CMSG_SPACE(sizeof(int))]; memset(buf, 0, sizeof buf);
    struct msghdr msg = { .msg_iov = &io, .msg_iovlen = 1,
                          .msg_control = buf, .msg_controllen = sizeof buf };
    struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
    c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_RIGHTS; c->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(c), &fd, sizeof(int));
    return sendmsg(sock, &msg, 0);
}
static int recv_fd(int sock, int *fd, struct meta *m) {
    struct iovec io = { .iov_base = m, .iov_len = sizeof *m };
    char buf[CMSG_SPACE(sizeof(int))]; memset(buf, 0, sizeof buf);
    struct msghdr msg = { .msg_iov = &io, .msg_iovlen = 1,
                          .msg_control = buf, .msg_controllen = sizeof buf };
    int n = recvmsg(sock, &msg, 0);
    if (n <= 0) return n;
    struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
    if (!c || c->cmsg_type != SCM_RIGHTS) { *fd = -1; return -1; }
    memcpy(fd, CMSG_DATA(c), sizeof(int));
    return n;
}

static void producer(int sock, int iters) {
    int fd = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
    struct gbm_device *gbm = gbm_create_device(fd);
    struct meta m; int W, H;
    char ackbuf[1];
    for (int i = 0; i < iters; i++) {
        // read requested size from importer (so it controls w/h), then make a bo
        if (read(sock, &m, sizeof m) != sizeof m) break;
        W = m.w; H = m.h;
        struct gbm_bo *bo = gbm_bo_create(gbm, W, H, DRM_FORMAT_ARGB8888, GBM_BO_USE_RENDERING);
        if (!bo) { fprintf(stderr, "producer: bo_create failed\n"); break; }
        int dfd = gbm_bo_get_fd(bo);
        struct meta out = { W, H, gbm_bo_get_stride(bo), gbm_bo_get_modifier(bo) };
        send_fd(sock, dfd, &out);
        close(dfd);
        if (read(sock, ackbuf, 1) != 1) { gbm_bo_destroy(bo); break; }  // wait import done
        gbm_bo_destroy(bo);
    }
    _exit(0);
}

static void sample(int iter) {
    char cmd[256];
    snprintf(cmd, sizeof cmd,
        "nvidia-smi 2>/dev/null | awk '/ %d /{for(i=1;i<=NF;i++) if($i ~ /MiB$/){print $i; exit}}'", getpid());
    FILE *g = popen(cmd, "r"); char mine[64]="?";
    if (g){ if(fgets(mine,sizeof mine,g)) mine[strcspn(mine,"\n")]=0; pclose(g);}
    FILE *t = popen("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits|head -1","r");
    char tot[64]="?"; if(t){ if(fgets(tot,sizeof tot,t)) tot[strcspn(tot,"\n")]=0; pclose(t);}
    printf("[xproc] iter=%-7d  importer_proc=%-8s  gpu_total_used_MiB=%s\n", iter, mine, tot);
    fflush(stdout);
}

static const char *vs_src =
  "attribute vec2 p; varying vec2 uv;"
  "void main(){ uv=p*0.5+0.5; gl_Position=vec4(p,0.0,1.0);} ";
static const char *fs_src =
  "precision mediump float; varying vec2 uv; uniform sampler2D t;"
  "void main(){ gl_FragColor=texture2D(t,uv);} ";

static GLuint mkprog(void){
    GLuint v=glCreateShader(GL_VERTEX_SHADER); glShaderSource(v,1,&vs_src,0); glCompileShader(v);
    GLuint f=glCreateShader(GL_FRAGMENT_SHADER); glShaderSource(f,1,&fs_src,0); glCompileShader(f);
    GLuint p=glCreateProgram(); glAttachShader(p,v); glAttachShader(p,f);
    glBindAttribLocation(p,0,"p"); glLinkProgram(p); return p;
}

int main(int argc, char **argv) {
    int iters = argc>1?atoi(argv[1]):60000;
    int draw  = argc>2?atoi(argv[2]):1;
    int W     = argc>3?atoi(argv[3]):1920;
    int H     = argc>4?atoi(argv[4]):1080;

    int sv[2];
    socketpair(AF_UNIX, SOCK_SEQPACKET, 0, sv);
    pid_t pid = fork();
    if (pid == 0) { close(sv[0]); producer(sv[1], iters); }
    close(sv[1]);
    int sock = sv[0];
    printf("xproc importer pid=%d  iters=%d draw=%d size=%dx%d\n", getpid(), iters, draw, W, H);

    int fd = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
    struct gbm_device *gbm = gbm_create_device(fd);
    eglGetPlatformDisplayEXT=(void*)eglGetProcAddress("eglGetPlatformDisplayEXT");
    eglCreateImageKHR=(void*)eglGetProcAddress("eglCreateImageKHR");
    eglDestroyImageKHR=(void*)eglGetProcAddress("eglDestroyImageKHR");
    glEGLImageTargetTexture2DOES=(void*)eglGetProcAddress("glEGLImageTargetTexture2DOES");
    EGLDisplay dpy = eglGetPlatformDisplayEXT(EGL_PLATFORM_GBM_KHR, gbm, NULL);
    eglInitialize(dpy,0,0); eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[]={EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RENDERABLE_TYPE,EGL_OPENGL_ES2_BIT,EGL_NONE};
    EGLConfig cfg; EGLint nc=0; eglChooseConfig(dpy,ca,&cfg,1,&nc);
    EGLint cta[]={EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE};
    EGLContext ctx=eglCreateContext(dpy,nc?cfg:EGL_NO_CONFIG_KHR,EGL_NO_CONTEXT,cta);
    eglMakeCurrent(dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,ctx);
    printf("EGL vendor=%s\n", eglQueryString(dpy,EGL_VENDOR));

    // render target + program for sampling
    GLuint prog=0, fbo=0, rt=0;
    if (draw) {
        prog=mkprog();
        glGenTextures(1,&rt); glBindTexture(GL_TEXTURE_2D,rt);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,64,64,0,GL_RGBA,GL_UNSIGNED_BYTE,0);
        glGenFramebuffers(1,&fbo); glBindFramebuffer(GL_FRAMEBUFFER,fbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,rt,0);
        glViewport(0,0,64,64);
    }
    const float quad[]={-1,-1, 1,-1, -1,1, 1,1};

    int fail=0; sample(0);
    int vary = argc>5?atoi(argv[5]):0;
    for (int i=1;i<=iters;i++) {
        int w=W, h=H;
        if (vary) { w = 640 + ((i*8) % 2400); h = 360 + ((i*5) % 1320); }  // many distinct sizes
        struct meta req={w,h,0,0};
        if (write(sock,&req,sizeof req)!=sizeof req) break;       // ask producer for a bo
        int dfd; struct meta m;
        if (recv_fd(sock,&dfd,&m)<=0) { fprintf(stderr,"recv_fd failed at %d\n",i); break; }
        EGLint attr[]={ EGL_WIDTH,m.w, EGL_HEIGHT,m.h,
            EGL_LINUX_DRM_FOURCC_EXT,DRM_FORMAT_ARGB8888,
            EGL_DMA_BUF_PLANE0_FD_EXT,dfd,
            EGL_DMA_BUF_PLANE0_OFFSET_EXT,0,
            EGL_DMA_BUF_PLANE0_PITCH_EXT,(EGLint)m.stride,
            EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT,(EGLint)(m.modifier&0xFFFFFFFF),
            EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT,(EGLint)(m.modifier>>32),
            EGL_IMAGE_PRESERVED_KHR,EGL_TRUE, EGL_NONE };
        EGLImageKHR img=eglCreateImageKHR(dpy,EGL_NO_CONTEXT,EGL_LINUX_DMA_BUF_EXT,NULL,attr);
        if (img==EGL_NO_IMAGE_KHR){ if(fail++<3) fprintf(stderr,"createImage fail %d: 0x%x\n",i,eglGetError()); }
        else {
            GLuint tex=0; glGenTextures(1,&tex); glBindTexture(GL_TEXTURE_2D,tex);
            glEGLImageTargetTexture2DOES(GL_TEXTURE_2D,img);
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
            if (draw) {
                glUseProgram(prog);
                glActiveTexture(GL_TEXTURE0);
                glVertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,0,quad);
                glEnableVertexAttribArray(0);
                glDrawArrays(GL_TRIANGLE_STRIP,0,4);
            }
            glBindTexture(GL_TEXTURE_2D,0);
            glDeleteTextures(1,&tex);
            eglDestroyImageKHR(dpy,img);
        }
        close(dfd);
        char a=1; if (write(sock,&a,1)!=1) break;                 // ack -> producer frees bo
        if (i%2000==0){ glFinish(); sample(i); }
    }
    glFinish(); sample(iters);
    if (fail) fprintf(stderr,"createImage failures: %d\n",fail);
    close(sock); waitpid(pid,0,0);
    return 0;
}
