#[derive(Default)]
pub struct Evidence {
    pub focused: bool,
    pub locked: bool,
    pub constraint: bool,
    pub configured: bool,
}

impl Evidence {
    pub fn validate(&self, command: &str) -> Result<(), &'static str> {
        if !self.configured {
            return Err("surface is not configured");
        }
        match command {
            "lock" if !self.focused || self.constraint => {
                Err("lock requires focus and no existing constraint")
            }
            "hint" if !self.locked => Err("hint requires an observed locked event"),
            "unlock" if !self.constraint => Err("no constraint to destroy"),
            "shape-default" | "shape-pointer" if !self.focused => {
                Err("cursor shape requires pointer focus")
            }
            _ => Ok(()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lock_requires_focus_and_configuration() {
        assert!(Evidence::default().validate("lock").is_err());
    }

    #[test]
    fn hint_requires_observed_lock_event() {
        let evidence = Evidence {
            configured: true,
            focused: true,
            constraint: true,
            ..Evidence::default()
        };
        assert!(evidence.validate("hint").is_err());
    }

    #[test]
    fn shape_requires_current_enter_serial() {
        let evidence = Evidence {
            configured: true,
            ..Evidence::default()
        };
        assert!(evidence.validate("shape-default").is_err());
    }

    #[test]
    fn duplicate_lock_is_rejected() {
        let evidence = Evidence {
            configured: true,
            focused: true,
            constraint: true,
            locked: true,
        };
        assert!(evidence.validate("lock").is_err());
    }

    #[test]
    fn constraint_can_be_destroyed_after_focus_leaves() {
        let evidence = Evidence {
            configured: true,
            constraint: true,
            ..Evidence::default()
        };
        assert!(evidence.validate("unlock").is_ok());
    }
}
