use vram_retest_gpu_client::{
    Format, Tranche, decode_formats, decode_indices, export_format, sampling_candidates,
};

#[test]
fn format_table_decodes_native_layout_and_keeps_modifier() {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&875713112u32.to_ne_bytes());
    bytes.extend_from_slice(&[0; 4]);
    bytes.extend_from_slice(&0x0100_0000_0000_0001u64.to_ne_bytes());
    assert_eq!(
        decode_formats(&bytes).unwrap(),
        vec![Format {
            code: 875713112,
            modifier: 0x0100_0000_0000_0001
        }]
    );
}

#[test]
fn malformed_feedback_is_rejected() {
    assert!(decode_formats(&[0; 15]).is_err());
    assert!(decode_indices(&[0]).is_err());
    assert!(decode_formats(&[]).is_err());
}

#[test]
fn selection_requires_sampling_tranche_on_requested_device() {
    let formats = vec![
        Format {
            code: 1,
            modifier: 0,
        },
        Format {
            code: 2,
            modifier: 7,
        },
    ];
    let tranches = vec![
        Tranche {
            device: 11,
            sampling: true,
            indices: vec![0],
        },
        Tranche {
            device: 22,
            sampling: false,
            indices: vec![0],
        },
        Tranche {
            device: 22,
            sampling: true,
            indices: vec![1],
        },
    ];
    assert_eq!(
        sampling_candidates(22, &formats, &tranches).unwrap(),
        vec![Format {
            code: 2,
            modifier: 7
        }]
    );
    assert!(sampling_candidates(33, &formats, &tranches).is_err());
}

#[test]
fn out_of_range_format_index_does_not_fall_back_to_other_gpu() {
    let tranches = vec![Tranche {
        device: 22,
        sampling: true,
        indices: vec![4],
    }];
    assert!(
        sampling_candidates(
            22,
            &[Format {
                code: 1,
                modifier: 0
            }],
            &tranches
        )
        .is_err()
    );
}

#[test]
fn native_indices_are_not_interpreted_as_bytes() {
    assert_eq!(decode_indices(&258u16.to_ne_bytes()).unwrap(), vec![258]);
}

#[test]
fn implicit_allocation_keeps_the_advertised_wire_modifier() {
    let requested = Format {
        code: 1,
        modifier: gbm::Modifier::Invalid.into(),
    };
    assert_eq!(
        export_format(
            requested,
            Format {
                code: 1,
                modifier: 7
            }
        )
        .unwrap(),
        requested
    );
}

#[test]
fn explicit_allocation_cannot_substitute_an_unadvertised_modifier() {
    assert!(
        export_format(
            Format {
                code: 1,
                modifier: 0
            },
            Format {
                code: 1,
                modifier: 7
            }
        )
        .is_err()
    );
    assert!(
        export_format(
            Format {
                code: 1,
                modifier: gbm::Modifier::Invalid.into()
            },
            Format {
                code: 2,
                modifier: 0
            }
        )
        .is_err()
    );
}
