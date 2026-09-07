use std::collections::BTreeSet;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Format {
    pub code: u32,
    pub modifier: u64,
}

pub fn export_format(requested: Format, actual: Format) -> Result<Format, String> {
    if requested.code != actual.code
        || (requested.modifier != u64::from(gbm::Modifier::Invalid)
            && requested.modifier != actual.modifier)
    {
        return Err(format!(
            "GBM allocation {actual:?} differs from request {requested:?}"
        ));
    }
    Ok(requested)
}

#[derive(Debug)]
pub struct Tranche {
    pub device: u64,
    pub sampling: bool,
    pub indices: Vec<usize>,
}

pub fn decode_formats(bytes: &[u8]) -> Result<Vec<Format>, String> {
    let (entries, remainder) = bytes.as_chunks::<16>();
    if entries.is_empty() || !remainder.is_empty() {
        return Err("invalid dmabuf format table length".into());
    }
    Ok(entries
        .iter()
        .map(|entry| Format {
            code: u32::from_ne_bytes(entry[..4].try_into().unwrap()),
            modifier: u64::from_ne_bytes(entry[8..].try_into().unwrap()),
        })
        .collect())
}

pub fn decode_indices(bytes: &[u8]) -> Result<Vec<usize>, String> {
    let (entries, remainder) = bytes.as_chunks::<2>();
    if !remainder.is_empty() {
        return Err("invalid dmabuf format index array length".into());
    }
    Ok(entries
        .iter()
        .map(|entry| usize::from(u16::from_ne_bytes(*entry)))
        .collect())
}

pub fn sampling_candidates(
    device: u64,
    formats: &[Format],
    tranches: &[Tranche],
) -> Result<Vec<Format>, String> {
    let mut seen = BTreeSet::new();
    let mut candidates = Vec::new();
    for tranche in tranches
        .iter()
        .filter(|tranche| tranche.device == device && tranche.sampling)
    {
        for index in &tranche.indices {
            let format = *formats
                .get(*index)
                .ok_or("dmabuf format index outside table")?;
            if seen.insert(format) {
                candidates.push(format);
            }
        }
    }
    if candidates.is_empty() {
        return Err(format!(
            "no advertised sampling formats for device {device}"
        ));
    }
    Ok(candidates)
}
