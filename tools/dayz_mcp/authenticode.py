"""WinVerifyTrust signer subject, with no UI and no revocation lookup.

The two Valve subjects are the ones measured on this host on 2026-09-29:
steam.exe and five of the six AddonBuilder-tree Steam DLLs use the Corp
certificate; CSERHelper.dll uses the shorter Valve certificate.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _Guid(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    )


class _WintrustFileInfo(ctypes.Structure):
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.POINTER(_Guid)),
    )


class _WintrustData(ctypes.Structure):
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", wintypes.LPVOID),
        ("pSIPClientData", wintypes.LPVOID),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("pFile", ctypes.POINTER(_WintrustFileInfo)),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
        ("pSignatureSettings", wintypes.LPVOID),
    )


class _CryptoBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p))


class _Algorithm(ctypes.Structure):
    _fields_ = (("pszObjId", ctypes.c_char_p), ("Parameters", _CryptoBlob))


class _FileTime(ctypes.Structure):
    _fields_ = (
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    )


class _CertInfo(ctypes.Structure):
    _fields_ = (
        ("dwVersion", wintypes.DWORD),
        ("SerialNumber", _CryptoBlob),
        ("SignatureAlgorithm", _Algorithm),
        ("Issuer", _CryptoBlob),
        ("NotBefore", _FileTime),
        ("NotAfter", _FileTime),
        ("Subject", _CryptoBlob),
    )


class _CertContext(ctypes.Structure):
    _fields_ = (
        ("dwCertEncodingType", wintypes.DWORD),
        ("pbCertEncoded", ctypes.c_void_p),
        ("cbCertEncoded", wintypes.DWORD),
        ("pCertInfo", ctypes.POINTER(_CertInfo)),
        ("hCertStore", ctypes.c_void_p),
    )


class _ProvCert(ctypes.Structure):
    _fields_ = (("cbStruct", wintypes.DWORD), ("pCert", ctypes.POINTER(_CertContext)))


_WINTRUST_ACTION_GENERIC_VERIFY_V2 = _Guid(
    0x00AAC56B,
    0xCD44,
    0x11D0,
    (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
)
_WTD_UI_NONE = 2
_WTD_REVOKE_NONE = 0
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_VERIFY = 1
_WTD_STATEACTION_CLOSE = 2
_WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000
_X509_ASN_ENCODING = 0x00000001
_CERT_X500_NAME_STR = 3
_CERT_NAME_STR_REVERSE_FLAG = 0x02000000

# Exact X.500 subjects from WinVerifyTrust on the measured Steam files.
VALVE_SIGNER_SUBJECTS = frozenset(
    {
        "CN=Valve Corp., O=Valve Corp., L=Bellevue, S=Washington, C=US",
        "CN=Valve, O=Valve, L=Bellevue, S=WA, C=US",
    }
)


def signer_subject(path: str) -> str | None:
    """Return the verified signer subject, or None if the signature is not valid."""
    if type(path) is not str or not path or "\0" in path:
        return None
    try:
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        verify = wintrust.WinVerifyTrust
        verify.argtypes = [wintypes.HWND, ctypes.POINTER(_Guid), wintypes.LPVOID]
        verify.restype = wintypes.LONG
        wintrust.WTHelperProvDataFromStateData.argtypes = [wintypes.HANDLE]
        wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
        wintrust.WTHelperGetProvSignerFromChain.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.c_void_p
        wintrust.WTHelperGetProvCertFromChain.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        wintrust.WTHelperGetProvCertFromChain.restype = ctypes.c_void_p
        crypt32.CertNameToStrW.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(_CryptoBlob),
            wintypes.DWORD,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        crypt32.CertNameToStrW.restype = wintypes.DWORD
    except (AttributeError, OSError):
        return None
    file_info = _WintrustFileInfo(
        ctypes.sizeof(_WintrustFileInfo),
        path,
        None,
        None,
    )
    trust_data = _WintrustData(
        ctypes.sizeof(_WintrustData),
        None,
        None,
        _WTD_UI_NONE,
        _WTD_REVOKE_NONE,
        _WTD_CHOICE_FILE,
        ctypes.pointer(file_info),
        _WTD_STATEACTION_VERIFY,
        None,
        None,
        _WTD_CACHE_ONLY_URL_RETRIEVAL,
        0,
        None,
    )
    subject_text: str | None = None
    try:
        status = verify(
            wintypes.HWND(-1),
            ctypes.byref(_WINTRUST_ACTION_GENERIC_VERIFY_V2),
            ctypes.byref(trust_data),
        )
        provider = signer = certificate = None
        if status == 0 and trust_data.hWVTStateData:
            provider = wintrust.WTHelperProvDataFromStateData(trust_data.hWVTStateData)
        if provider:
            signer = wintrust.WTHelperGetProvSignerFromChain(provider, 0, False, 0)
        if signer:
            certificate = wintrust.WTHelperGetProvCertFromChain(signer, 0)
        if certificate:
            context = ctypes.cast(
                certificate, ctypes.POINTER(_ProvCert)
            ).contents.pCert
            if context and context.contents.pCertInfo:
                subject = context.contents.pCertInfo.contents.Subject
                buffer = ctypes.create_unicode_buffer(4096)
                written = crypt32.CertNameToStrW(
                    _X509_ASN_ENCODING,
                    ctypes.byref(subject),
                    _CERT_X500_NAME_STR | _CERT_NAME_STR_REVERSE_FLAG,
                    buffer,
                    len(buffer),
                )
                if written > 1 and buffer.value:
                    subject_text = buffer.value
    except (OSError, ValueError):
        subject_text = None
    finally:
        trust_data.dwStateAction = _WTD_STATEACTION_CLOSE
        try:
            verify(
                wintypes.HWND(-1),
                ctypes.byref(_WINTRUST_ACTION_GENERIC_VERIFY_V2),
                ctypes.byref(trust_data),
            )
        except (OSError, ValueError):
            subject_text = None
    return subject_text


def is_valve_signed(path: str) -> bool:
    """True only when the file's verified signer subject is one of Valve's."""
    try:
        return signer_subject(path) in VALVE_SIGNER_SUBJECTS
    except Exception:
        return False
