// Standalone reproducer for the NVIDIA dmabuf/EGLImage VRAM-reclaim question.
//
// Mimics a Wayland compositor's client-buffer import hot path with ZERO
// compositor code:
//   GBM bo -> export dmabuf -> eglCreateImageKHR(EGL_LINUX_DMA_BUF_EXT)
//   -> [optional] bind to a GLES texture (EGLImageTargetTexture2DOES)
//   -> destroy image -> delete texture -> free bo,  in a loop.
//
// If this process's nvidia-smi GPU memory climbs monotonically with the
// iteration count, the driver is not reclaiming VRAM on import churn — a
// driver-level leak, independent of the compositor.
//
// Modes (argv): repro <iters> <mode> <w> <h>
//   mode 0: reuse ONE dmabuf, create/destroy EGLImage only (purest EGLImage churn)
//   mode 1: fresh dmabuf each iter, create/destroy EGLImage only (validation path)
//   mode 2: fresh dmabuf each iter, + bind to texture + delete (full render path)

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>

#include <gbm.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>

static PFNEGLGETPLATFORMDISPLAYEXTPROC eglGetPlatformDisplayEXT;
static PFNEGLCREATEIMAGEKHRPROC eglCreateImageKHR;
static PFNEGLDESTROYIMAGEKHRPROC eglDestroyImageKHR;
static PFNGLEGLIMAGETARGETTEXTURE2DOESPROC glEGLImageTargetTexture2DOES;

#define DRM_FORMAT_ARGB8888 0x34325241

static void sample(const char *tag, int iter) {
    char cmd[256];
    // Per-process GPU memory for our PID from the nvidia-smi process table.
    snprintf(cmd, sizeof cmd,
        "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1");
    FILE *f = popen(cmd, "r");
    char total[64] = "?";
    if (f) { if (fgets(total, sizeof total, f)) total[strcspn(total,"\n")]=0; pclose(f); }
    // Our own process row (graphics process), MiB.
    char cmd2[256];
    snprintf(cmd2, sizeof cmd2,
        "nvidia-smi 2>/dev/null | awk '/ %d /{for(i=1;i<=NF;i++) if($i ~ /MiB$/){print $i; exit}}'",
        getpid());
    FILE *g = popen(cmd2, "r");
    char mine[64] = "?";
    if (g) { if (fgets(mine, sizeof mine, g)) mine[strcspn(mine,"\n")]=0; pclose(g); }
    printf("[%-6s] iter=%-7d  this_proc=%-8s  gpu_total_used_MiB=%s\n", tag, iter, mine, total);
    fflush(stdout);
}

int main(int argc, char **argv) {
    int iters = argc > 1 ? atoi(argv[1]) : 20000;
    int mode  = argc > 2 ? atoi(argv[2]) : 2;
    int W     = argc > 3 ? atoi(argv[3]) : 1920;
    int H     = argc > 4 ? atoi(argv[4]) : 1080;

    printf("repro: iters=%d mode=%d size=%dx%d pid=%d\n", iters, mode, W, H, getpid());

    int fd = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("open renderD128"); return 1; }
    struct gbm_device *gbm = gbm_create_device(fd);
    if (!gbm) { fprintf(stderr, "gbm_create_device failed\n"); return 1; }

    eglGetPlatformDisplayEXT = (void*)eglGetProcAddress("eglGetPlatformDisplayEXT");
    eglCreateImageKHR        = (void*)eglGetProcAddress("eglCreateImageKHR");
    eglDestroyImageKHR       = (void*)eglGetProcAddress("eglDestroyImageKHR");
    glEGLImageTargetTexture2DOES = (void*)eglGetProcAddress("glEGLImageTargetTexture2DOES");
    if (!eglGetPlatformDisplayEXT || !eglCreateImageKHR || !eglDestroyImageKHR) {
        fprintf(stderr, "missing EGL extension entry points\n"); return 1;
    }

    EGLDisplay dpy = eglGetPlatformDisplayEXT(EGL_PLATFORM_GBM_KHR, gbm, NULL);
    if (dpy == EGL_NO_DISPLAY) { fprintf(stderr, "no EGL display\n"); return 1; }
    EGLint major, minor;
    if (!eglInitialize(dpy, &major, &minor)) { fprintf(stderr, "eglInitialize failed\n"); return 1; }
    printf("EGL %d.%d  vendor=%s\n", major, minor, eglQueryString(dpy, EGL_VENDOR));
    printf("EXT image_dma_buf_import: %s\n",
        strstr(eglQueryString(dpy, EGL_EXTENSIONS), "EGL_EXT_image_dma_buf_import") ? "yes":"NO");

    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint cfg_attr[] = { EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
                          EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_NONE };
    EGLConfig cfg; EGLint n = 0;
    eglChooseConfig(dpy, cfg_attr, &cfg, 1, &n);
    EGLint ctx_attr[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext ctx = eglCreateContext(dpy, n ? cfg : EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ctx_attr);
    if (ctx == EGL_NO_CONTEXT) { fprintf(stderr, "eglCreateContext failed 0x%x\n", eglGetError()); return 1; }
    if (!eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) {
        fprintf(stderr, "eglMakeCurrent failed 0x%x\n", eglGetError()); return 1;
    }

    // Persistent bo for mode 0.
    struct gbm_bo *bo0 = NULL;
    if (mode == 0) {
        bo0 = gbm_bo_create(gbm, W, H, DRM_FORMAT_ARGB8888, GBM_BO_USE_RENDERING);
        if (!bo0) { fprintf(stderr, "gbm_bo_create (mode0) failed\n"); return 1; }
    }

    int fail = 0;
    sample("start", 0);
    for (int i = 1; i <= iters; i++) {
        struct gbm_bo *bo = bo0;
        if (mode != 0) {
            bo = gbm_bo_create(gbm, W, H, DRM_FORMAT_ARGB8888, GBM_BO_USE_RENDERING);
            if (!bo) { fprintf(stderr, "gbm_bo_create failed at %d\n", i); break; }
        }
        int dmabuf_fd = gbm_bo_get_fd(bo);
        uint32_t stride = gbm_bo_get_stride(bo);
        uint64_t mod = gbm_bo_get_modifier(bo);

        EGLint attribs[] = {
            EGL_WIDTH, W,
            EGL_HEIGHT, H,
            EGL_LINUX_DRM_FOURCC_EXT, DRM_FORMAT_ARGB8888,
            EGL_DMA_BUF_PLANE0_FD_EXT, dmabuf_fd,
            EGL_DMA_BUF_PLANE0_OFFSET_EXT, 0,
            EGL_DMA_BUF_PLANE0_PITCH_EXT, (EGLint)stride,
            EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, (EGLint)(mod & 0xFFFFFFFF),
            EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT, (EGLint)(mod >> 32),
            EGL_IMAGE_PRESERVED_KHR, EGL_TRUE,
            EGL_NONE
        };
        EGLImageKHR img = eglCreateImageKHR(dpy, EGL_NO_CONTEXT,
                                            EGL_LINUX_DMA_BUF_EXT, NULL, attribs);
        if (img == EGL_NO_IMAGE_KHR) {
            if (fail++ < 3) fprintf(stderr, "eglCreateImageKHR failed at %d: 0x%x\n", i, eglGetError());
        } else {
            if (mode == 2) {
                GLuint tex = 0;
                glGenTextures(1, &tex);
                glBindTexture(GL_TEXTURE_2D, tex);
                glEGLImageTargetTexture2DOES(GL_TEXTURE_2D, img);
                glBindTexture(GL_TEXTURE_2D, 0);
                glDeleteTextures(1, &tex);
            }
            eglDestroyImageKHR(dpy, img);
        }
        close(dmabuf_fd);
        if (mode != 0) gbm_bo_destroy(bo);

        if (i % 2000 == 0) { glFinish(); sample("run", i); }
    }
    glFinish();
    sample("end", iters);
    if (fail) fprintf(stderr, "total eglCreateImageKHR failures: %d\n", fail);
    return 0;
}
