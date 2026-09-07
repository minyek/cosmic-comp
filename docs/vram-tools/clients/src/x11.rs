use x11rb::{
    connection::Connection,
    protocol::xproto::{AtomEnum, ConnectionExt, CreateWindowAux, PropMode, WindowClass},
    rust_connection::RustConnection,
    wrapper::ConnectionExt as _,
};

pub struct StartupWindow {
    connection: RustConnection,
    window: u32,
}

impl StartupWindow {
    pub fn map(token: &str, app_id: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let (connection, screen) = x11rb::connect(None)?;
        let root = &connection.setup().roots[screen];
        let window = connection.generate_id()?;
        connection
            .create_window(
                root.root_depth,
                window,
                root.root,
                0,
                0,
                160,
                120,
                0,
                WindowClass::INPUT_OUTPUT,
                root.root_visual,
                &CreateWindowAux::new().background_pixel(root.white_pixel),
            )?
            .check()?;
        let startup = connection
            .intern_atom(false, b"_NET_STARTUP_ID")?
            .reply()?
            .atom;
        let utf8 = connection.intern_atom(false, b"UTF8_STRING")?.reply()?.atom;
        connection
            .change_property8(PropMode::REPLACE, window, startup, utf8, token.as_bytes())?
            .check()?;
        connection
            .change_property8(
                PropMode::REPLACE,
                window,
                AtomEnum::WM_NAME,
                AtomEnum::STRING,
                app_id.as_bytes(),
            )?
            .check()?;
        connection
            .change_property8(
                PropMode::REPLACE,
                window,
                AtomEnum::WM_CLASS,
                AtomEnum::STRING,
                format!("{app_id}\0{app_id}\0").as_bytes(),
            )?
            .check()?;
        connection.map_window(window)?.check()?;
        connection.flush()?;
        Ok(Self { connection, window })
    }

    pub fn destroy(self) -> Result<(), Box<dyn std::error::Error>> {
        self.connection.destroy_window(self.window)?.check()?;
        self.connection.flush()?;
        Ok(())
    }
}
