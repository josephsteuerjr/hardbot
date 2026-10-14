//! Same user-bound DPAPI format as the Python manager, including legacy reads.
use base64::{engine::general_purpose::STANDARD, Engine};

const PREFIX: &str = "dpapi-v1:";
/// Публичный маркер запечатанного значения: settings.save() переписывает такие
/// токены как есть, не пытаясь запечатать повторно и не теряя их.
pub const SEALED_PREFIX: &str = PREFIX;

#[repr(C)]
struct Blob { size: u32, data: *mut u8 }

#[link(name = "crypt32")]
extern "system" {
    fn CryptProtectData(input: *const Blob, description: *const u16, entropy: *const Blob,
        reserved: *mut std::ffi::c_void, prompt: *mut std::ffi::c_void, flags: u32, output: *mut Blob) -> i32;
    fn CryptUnprotectData(input: *const Blob, description: *mut *mut u16, entropy: *const Blob,
        reserved: *mut std::ffi::c_void, prompt: *mut std::ffi::c_void, flags: u32, output: *mut Blob) -> i32;
}
#[link(name = "kernel32")]
extern "system" { fn LocalFree(memory: *mut std::ffi::c_void) -> *mut std::ffi::c_void; }

fn dpapi(bytes: &[u8], decrypt: bool) -> Result<Vec<u8>, std::io::Error> {
    let input = Blob { size: bytes.len() as u32, data: bytes.as_ptr() as *mut u8 };
    let mut output = Blob { size: 0, data: std::ptr::null_mut() };
    let nil: *mut std::ffi::c_void = std::ptr::null_mut();
    let ok = unsafe {
        if decrypt { CryptUnprotectData(&input, std::ptr::null_mut(), std::ptr::null(), nil, nil, 1, &mut output) }
        else { CryptProtectData(&input, std::ptr::null(), std::ptr::null(), nil, nil, 1, &mut output) }
    };
    if ok == 0 { return Err(std::io::Error::last_os_error()); }
    let result = unsafe { std::slice::from_raw_parts(output.data, output.size as usize).to_vec() };
    unsafe { LocalFree(output.data as *mut std::ffi::c_void); }
    Ok(result)
}

pub fn seal(token: &str) -> Result<String, std::io::Error> {
    Ok(format!("{}{}", PREFIX, STANDARD.encode(dpapi(token.as_bytes(), false)?)))
}
pub fn open(token: &str) -> Result<String, std::io::Error> {
    if let Some(encoded) = token.strip_prefix(PREFIX) {
        let bytes = STANDARD.decode(encoded).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        String::from_utf8(dpapi(&bytes, true)?).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
    } else { Ok(token.to_owned()) }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_and_dpapi_roundtrip() {
        assert_eq!(open("synthetic-token").unwrap(), "synthetic-token");
        let ciphertext = seal("synthetic-token").unwrap();
        assert!(!ciphertext.contains("synthetic-token"));
        assert_eq!(open(&ciphertext).unwrap(), "synthetic-token");
        assert!(open("dpapi-v1:invalid").is_err());
    }
}
