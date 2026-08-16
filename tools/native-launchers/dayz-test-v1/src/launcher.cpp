#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
#include <stdint.h>

#include "../generated/closure_manifest.h"
#include "../generated/addon_roots.h"

namespace {

constexpr DWORD kMaxFrameBytes = 65536;
constexpr BYTE kBrokerVersion = 1;
constexpr char kBrokerMagic[] = "DZM1";
constexpr char kAnnouncementMagic[] = "DZA1";
constexpr char kWorkerTerminalMagic[] = "DZW1";
constexpr DWORD kHeaderBytes = 48;
constexpr DWORD kHashBytes = 32;
constexpr DWORD kMaxPinnedHandles = 192;

enum class LaunchKind : BYTE {
    PRIVATE_WORKER = 1,
    LIFECYCLE_CLI = 2,
    ADDON_BUILDER = 3,
};

#pragma pack(push, 1)
struct BrokerHeader {
    BYTE magic[4];
    BYTE version;
    BYTE kind;
    uint16_t flags;
    uint32_t payload_bytes;
    uint32_t stdin_bytes;
    BYTE stdin_sha256[kHashBytes];
};
#pragma pack(pop)

static_assert(sizeof(BrokerHeader) == kHeaderBytes, "broker header drift");

struct PinnedClosure {
    HANDLE handles[kMaxPinnedHandles];
    DWORD count;
};

struct SecretState {
    wchar_t* identity;
    DWORD identity_chars;
    wchar_t* token;
    DWORD token_chars;
    wchar_t* normal_policy;
    DWORD normal_policy_chars;
};

struct ChildOutput {
    BYTE* bytes;
    DWORD size;
    DWORD exit_code;
};

struct AddonRequest {
    bool clear;
    bool pack_only;
    wchar_t prefix[65];
    wchar_t source[521];
    wchar_t target[521];
    wchar_t temp[521];
};

struct PboSnapshot {
    bool exists;
    FILE_ID_INFO identity;
    FILE_BASIC_INFO basic;
    FILE_STANDARD_INFO standard;
    BYTE sha256[kHashBytes];
};

#pragma pack(push, 1)
struct BrokerAnnouncement {
    BYTE magic[4];
    BYTE version;
    BYTE kind;
    uint16_t flags;
    uint32_t sequence;
    uint32_t path_bytes;
    BYTE image_sha256[32];
    uint64_t volume_serial_number;
    BYTE file_id[16];
};
#pragma pack(pop)

static_assert(sizeof(BrokerAnnouncement) == 72, "announcement header drift");
volatile LONG gAnnouncementSequence = 0;

extern "C" __declspec(selectany) const char kManifestMarker[] =
    "DAYZ_MCP_MANIFEST_SHA256=" DAYZ_MCP_MANIFEST_SHA256_HEX;

void SecureZero(void* value, SIZE_T bytes) {
    if (value != nullptr && bytes != 0) {
        SecureZeroMemory(value, bytes);
    }
}

bool SameBytes(const BYTE* left, const BYTE* right, DWORD count) {
    BYTE different = 0;
    for (DWORD index = 0; index < count; ++index) {
        different |= static_cast<BYTE>(left[index] ^ right[index]);
    }
    return different == 0;
}

bool IsApprovedKind(BYTE value) {
    return value == static_cast<BYTE>(LaunchKind::PRIVATE_WORKER) ||
           value == static_cast<BYTE>(LaunchKind::LIFECYCLE_CLI) ||
           value == static_cast<BYTE>(LaunchKind::ADDON_BUILDER);
}

bool AppendText(wchar_t* destination, DWORD capacity, const wchar_t* text) {
    DWORD used = 0;
    while (used < capacity && destination[used] != L'\0') {
        ++used;
    }
    DWORD index = 0;
    while (text[index] != L'\0') {
        if (used + index + 1 >= capacity) {
            return false;
        }
        destination[used + index] = text[index];
        ++index;
    }
    destination[used + index] = L'\0';
    return true;
}

bool CopyText(wchar_t* destination, DWORD capacity, const wchar_t* text) {
    if (capacity == 0) {
        return false;
    }
    destination[0] = L'\0';
    return AppendText(destination, capacity, text);
}

bool BundleRoot(wchar_t* destination, DWORD capacity) {
    DWORD length = GetModuleFileNameW(nullptr, destination, capacity);
    if (length == 0 || length >= capacity) {
        return false;
    }
    while (length != 0 && destination[length - 1] != L'\\') {
        --length;
    }
    if (length == 0) {
        return false;
    }
    destination[length - 1] = L'\0';
    return true;
}

bool BundlePath(const wchar_t* relative, wchar_t* destination, DWORD capacity) {
    return BundleRoot(destination, capacity) &&
           AppendText(destination, capacity, L"\\") &&
           AppendText(destination, capacity, relative);
}

bool SamePathText(const wchar_t* left, const wchar_t* right) {
    return CompareStringOrdinal(left, -1, right, -1, TRUE) == CSTR_EQUAL;
}

bool SameBundlePathText(const wchar_t* left, const wchar_t* right) {
    DWORD index = 0;
    while (left[index] != L'\0' && right[index] != L'\0') {
        wchar_t left_character = left[index] == L'/' ? L'\\' : left[index];
        wchar_t right_character = right[index] == L'/' ? L'\\' : right[index];
        if (CompareStringOrdinal(
                &left_character, 1, &right_character, 1, TRUE) != CSTR_EQUAL) {
            return false;
        }
        ++index;
    }
    return left[index] == right[index];
}

bool ExpectedBundlePath(const wchar_t* relative) {
    static const wchar_t* metadata[] = {
        L"closure-manifest.json",
        L"dayz-test-launcher.exe",
        L"reproducibility.json",
    };
    for (DWORD index = 0; index < ARRAYSIZE(metadata); ++index) {
        if (SameBundlePathText(relative, metadata[index])) return true;
    }
    for (DWORD index = 0; index < kClosureEntryCount; ++index) {
        if (kClosureEntries[index].kind == ClosureKind::BUNDLE &&
            SameBundlePathText(relative, kClosureEntries[index].path)) {
            return true;
        }
    }
    return false;
}

bool ValidateBundleDirectory(const wchar_t* relative) {
    wchar_t search[32768]{};
    if (!BundleRoot(search, ARRAYSIZE(search)) ||
        (relative[0] != L'\0' &&
         (!AppendText(search, ARRAYSIZE(search), L"\\") ||
          !AppendText(search, ARRAYSIZE(search), relative))) ||
        !AppendText(search, ARRAYSIZE(search), L"\\*")) {
        return false;
    }
    WIN32_FIND_DATAW data{};
    HANDLE find = FindFirstFileW(search, &data);
    SecureZero(search, sizeof(search));
    if (find == INVALID_HANDLE_VALUE) return false;
    bool ok = true;
    do {
        if ((data.cFileName[0] == L'.' && data.cFileName[1] == L'\0') ||
            (data.cFileName[0] == L'.' && data.cFileName[1] == L'.' &&
             data.cFileName[2] == L'\0')) {
            continue;
        }
        if ((data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            ok = false;
            break;
        }
        wchar_t child[32768]{};
        if ((relative[0] != L'\0' &&
             (!CopyText(child, ARRAYSIZE(child), relative) ||
              !AppendText(child, ARRAYSIZE(child), L"\\"))) ||
            !AppendText(child, ARRAYSIZE(child), data.cFileName)) {
            ok = false;
            break;
        }
        if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
            ok = ValidateBundleDirectory(child);
        } else {
            ok = ExpectedBundlePath(child);
        }
        SecureZero(child, sizeof(child));
        if (!ok) break;
    } while (FindNextFileW(find, &data));
    DWORD error = GetLastError();
    FindClose(find);
    return ok && error == ERROR_NO_MORE_FILES;
}

bool HashBuffer(const BYTE* data, DWORD data_bytes, BYTE digest[kHashBytes]) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    DWORD object_bytes = 0;
    DWORD result_bytes = 0;
    BYTE* object = nullptr;
    bool ok = false;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0 ||
        BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                          reinterpret_cast<BYTE*>(&object_bytes), sizeof(object_bytes),
                          &result_bytes, 0) < 0 ||
        object_bytes == 0) {
        goto cleanup;
    }
    object = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, object_bytes));
    if (object == nullptr ||
        BCryptCreateHash(algorithm, &hash, object, object_bytes, nullptr, 0, 0) < 0 ||
        (data_bytes != 0 && BCryptHashData(hash, const_cast<BYTE*>(data), data_bytes, 0) < 0) ||
        BCryptFinishHash(hash, digest, kHashBytes, 0) < 0) {
        goto cleanup;
    }
    ok = true;
cleanup:
    if (hash != nullptr) {
        BCryptDestroyHash(hash);
    }
    if (object != nullptr) {
        SecureZero(object, object_bytes);
        HeapFree(GetProcessHeap(), 0, object);
    }
    if (algorithm != nullptr) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    return ok;
}

bool HashHandle(HANDLE file, BYTE digest[kHashBytes]) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    DWORD object_bytes = 0;
    DWORD result_bytes = 0;
    BYTE* object = nullptr;
    BYTE buffer[64 * 1024];
    bool ok = false;
    LARGE_INTEGER start{};
    if (!SetFilePointerEx(file, start, nullptr, FILE_BEGIN) ||
        BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0 ||
        BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                          reinterpret_cast<BYTE*>(&object_bytes), sizeof(object_bytes),
                          &result_bytes, 0) < 0 || object_bytes == 0) {
        goto cleanup;
    }
    object = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, object_bytes));
    if (object == nullptr ||
        BCryptCreateHash(algorithm, &hash, object, object_bytes, nullptr, 0, 0) < 0) {
        goto cleanup;
    }
    for (;;) {
        DWORD read = 0;
        if (!ReadFile(file, buffer, sizeof(buffer), &read, nullptr)) {
            goto cleanup;
        }
        if (read == 0) {
            break;
        }
        if (BCryptHashData(hash, buffer, read, 0) < 0) {
            goto cleanup;
        }
    }
    if (BCryptFinishHash(hash, digest, kHashBytes, 0) < 0) {
        goto cleanup;
    }
    ok = true;
cleanup:
    SecureZero(buffer, sizeof(buffer));
    if (hash != nullptr) {
        BCryptDestroyHash(hash);
    }
    if (object != nullptr) {
        SecureZero(object, object_bytes);
        HeapFree(GetProcessHeap(), 0, object);
    }
    if (algorithm != nullptr) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    SetFilePointerEx(file, start, nullptr, FILE_BEGIN);
    return ok;
}

int HexDigit(char value) {
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'A' && value <= 'F') return value - 'A' + 10;
    return -1;
}

bool MarkerDigest(BYTE digest[kHashBytes]) {
    const char* hex = kManifestMarker + 25;
    for (DWORD index = 0; index < kHashBytes; ++index) {
        int high = HexDigit(hex[index * 2]);
        int low = HexDigit(hex[index * 2 + 1]);
        if (high < 0 || low < 0) {
            return false;
        }
        digest[index] = static_cast<BYTE>((high << 4) | low);
    }
    return hex[64] == '\0';
}

void ClosePinned(PinnedClosure* closure) {
    if (closure == nullptr) return;
    while (closure->count != 0) {
        --closure->count;
        CloseHandle(closure->handles[closure->count]);
        closure->handles[closure->count] = nullptr;
    }
}

bool PinEntry(const ClosureEntry& expected, PinnedClosure* closure) {
    if (closure->count >= kMaxPinnedHandles) {
        return false;
    }
    wchar_t path[32768]{};
    if (expected.kind == ClosureKind::BUNDLE) {
        if (!BundlePath(expected.path, path, ARRAYSIZE(path))) return false;
    } else if (expected.kind == ClosureKind::EXTERNAL) {
        if (!CopyText(path, ARRAYSIZE(path), expected.path)) return false;
    } else {
        return false;
    }
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                              FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }
    FILE_ATTRIBUTE_TAG_INFO tag{};
    FILE_STANDARD_INFO standard{};
    FILE_ID_INFO identity{};
    BYTE digest[kHashBytes]{};
    bool ok = GetFileType(file) == FILE_TYPE_DISK &&
              GetFileInformationByHandleEx(file, FileAttributeTagInfo, &tag, sizeof(tag)) &&
              GetFileInformationByHandleEx(file, FileStandardInfo, &standard, sizeof(standard)) &&
              GetFileInformationByHandleEx(file, FileIdInfo, &identity, sizeof(identity)) &&
              (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0 &&
              !standard.Directory && !standard.DeletePending && standard.NumberOfLinks == 1 &&
              static_cast<uint64_t>(standard.EndOfFile.QuadPart) == expected.size &&
              HashHandle(file, digest) && SameBytes(digest, expected.sha256, kHashBytes);
    if (ok && expected.require_identity) {
        ok = identity.VolumeSerialNumber == expected.volume_serial_number &&
             SameBytes(identity.FileId.Identifier, expected.file_id, 16);
    }
    SecureZero(digest, sizeof(digest));
    SecureZero(path, sizeof(path));
    if (!ok) {
        CloseHandle(file);
        return false;
    }
    closure->handles[closure->count++] = file;
    return true;
}

bool ValidateClosure(PinnedClosure* closure) {
    closure->count = 0;
    if (!ValidateBundleDirectory(L"")) {
        return false;
    }
    wchar_t manifest_path[32768]{};
    if (!BundlePath(L"closure-manifest.json", manifest_path, ARRAYSIZE(manifest_path))) {
        return false;
    }
    HANDLE manifest = CreateFileW(manifest_path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                                  OPEN_EXISTING,
                                  FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (manifest == INVALID_HANDLE_VALUE) {
        return false;
    }
    FILE_ATTRIBUTE_TAG_INFO tag{};
    FILE_STANDARD_INFO standard{};
    BYTE actual[kHashBytes]{};
    BYTE expected[kHashBytes]{};
    bool ok = GetFileInformationByHandleEx(manifest, FileAttributeTagInfo, &tag, sizeof(tag)) &&
              GetFileInformationByHandleEx(manifest, FileStandardInfo, &standard, sizeof(standard)) &&
              (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0 &&
              !standard.Directory && !standard.DeletePending && standard.NumberOfLinks == 1 &&
              HashHandle(manifest, actual) && MarkerDigest(expected) &&
              SameBytes(actual, expected, kHashBytes);
    SecureZero(actual, sizeof(actual));
    SecureZero(expected, sizeof(expected));
    SecureZero(manifest_path, sizeof(manifest_path));
    if (!ok || closure->count >= kMaxPinnedHandles) {
        CloseHandle(manifest);
        return false;
    }
    closure->handles[closure->count++] = manifest;
    for (DWORD index = 0; index < kClosureEntryCount; ++index) {
        if (!PinEntry(kClosureEntries[index], closure)) {
            ClosePinned(closure);
            return false;
        }
    }
    return true;
}

bool ReadExact(HANDLE input, BYTE* destination, DWORD bytes) {
    DWORD offset = 0;
    while (offset < bytes) {
        DWORD read = 0;
        if (!ReadFile(input, destination + offset, bytes - offset, &read, nullptr) || read == 0) {
            return false;
        }
        offset += read;
    }
    return true;
}

bool ValidatePayloadByKind(const BrokerHeader& header, const BYTE* payload);

bool ReadBrokerFrame(BrokerHeader* header, BYTE** body) {
    *body = nullptr;
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (input == nullptr || input == INVALID_HANDLE_VALUE ||
        !ReadExact(input, reinterpret_cast<BYTE*>(header), sizeof(*header)) ||
        header->magic[0] != kBrokerMagic[0] || header->magic[1] != kBrokerMagic[1] ||
        header->magic[2] != kBrokerMagic[2] || header->magic[3] != kBrokerMagic[3] ||
        header->version != kBrokerVersion || header->flags != 0 ||
        !IsApprovedKind(header->kind)) {
        return false;
    }
    uint64_t total = static_cast<uint64_t>(sizeof(*header)) +
                     header->payload_bytes + header->stdin_bytes;
    if (total > kMaxFrameBytes || header->payload_bytes == 0) {
        return false;
    }
    DWORD body_bytes = header->payload_bytes + header->stdin_bytes;
    BYTE* value = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, body_bytes));
    if (value == nullptr || !ReadExact(input, value, body_bytes)) {
        if (value != nullptr) HeapFree(GetProcessHeap(), 0, value);
        return false;
    }
    BYTE digest[kHashBytes]{};
    bool ok = HashBuffer(value + header->payload_bytes, header->stdin_bytes, digest) &&
              SameBytes(digest, header->stdin_sha256, kHashBytes) &&
              ValidatePayloadByKind(*header, value);
    SecureZero(digest, sizeof(digest));
    if (!ok) {
        SecureZero(value, body_bytes);
        HeapFree(GetProcessHeap(), 0, value);
        return false;
    }
    *body = value;
    return true;
}

bool CaptureEnvironmentValue(const wchar_t* name, wchar_t** value, DWORD* chars) {
    *value = nullptr;
    *chars = 0;
    DWORD required = GetEnvironmentVariableW(name, nullptr, 0);
    if (required < 2 || required > 8192) return false;
    auto* captured = static_cast<wchar_t*>(
        HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, required * sizeof(wchar_t)));
    if (captured == nullptr || GetEnvironmentVariableW(name, captured, required) + 1 != required) {
        if (captured != nullptr) HeapFree(GetProcessHeap(), 0, captured);
        return false;
    }
    SetEnvironmentVariableW(name, nullptr);
    *value = captured;
    *chars = required;
    return true;
}

bool CaptureSecrets(SecretState* secrets) {
    secrets->identity = nullptr;
    secrets->identity_chars = 0;
    secrets->token = nullptr;
    secrets->token_chars = 0;
    secrets->normal_policy = nullptr;
    secrets->normal_policy_chars = 0;
    bool identity_ok = CaptureEnvironmentValue(L"DAYZ_MCP_CLIENT_ID_JSON", &secrets->identity,
                                                &secrets->identity_chars);
    bool token_ok = CaptureEnvironmentValue(L"DAYZ_MCP_LEASE_TOKEN", &secrets->token,
                                             &secrets->token_chars);
    bool policy_ok = CaptureEnvironmentValue(L"DAYZ_MCP_NORMAL_POLICY_JSON",
                                              &secrets->normal_policy,
                                              &secrets->normal_policy_chars);
    SetEnvironmentVariableW(L"DAYZ_MCP_CLIENT_ID_JSON", nullptr);
    SetEnvironmentVariableW(L"DAYZ_MCP_LEASE_TOKEN", nullptr);
    SetEnvironmentVariableW(L"DAYZ_MCP_NORMAL_POLICY_JSON", nullptr);
    return identity_ok && token_ok && policy_ok;
}

void DestroySecrets(SecretState* secrets) {
    if (secrets->identity != nullptr) {
        SecureZero(secrets->identity, secrets->identity_chars * sizeof(wchar_t));
        HeapFree(GetProcessHeap(), 0, secrets->identity);
    }
    if (secrets->token != nullptr) {
        SecureZero(secrets->token, secrets->token_chars * sizeof(wchar_t));
        HeapFree(GetProcessHeap(), 0, secrets->token);
    }
    if (secrets->normal_policy != nullptr) {
        SecureZero(secrets->normal_policy,
                   secrets->normal_policy_chars * sizeof(wchar_t));
        HeapFree(GetProcessHeap(), 0, secrets->normal_policy);
    }
    SecureZero(secrets, sizeof(*secrets));
}

bool AppendEnvironmentEntry(wchar_t* block, DWORD capacity, DWORD* position,
                            const wchar_t* name, const wchar_t* value) {
    const wchar_t* values[] = {name, L"=", value};
    for (DWORD item = 0; item < ARRAYSIZE(values); ++item) {
        for (DWORD index = 0; values[item][index] != L'\0'; ++index) {
            if (*position + 2 >= capacity) return false;
            block[(*position)++] = values[item][index];
        }
    }
    block[(*position)++] = L'\0';
    return true;
}

bool UnsignedToDecimal(uintptr_t value, wchar_t* output, DWORD capacity) {
    if (output == nullptr || capacity < 2) return false;
    DWORD digits = 0;
    do {
        if (digits + 1 >= capacity) return false;
        output[digits++] = static_cast<wchar_t>(L'0' + value % 10);
        value /= 10;
    } while (value != 0);
    for (DWORD left = 0, right = digits - 1; left < right; ++left, --right) {
        wchar_t swap = output[left];
        output[left] = output[right];
        output[right] = swap;
    }
    output[digits] = L'\0';
    return true;
}

bool IsLocalAbsolutePath(const wchar_t* value);

bool MinimalEnvironment(LaunchKind kind, const SecretState* secrets,
                        const wchar_t* private_temp, HANDLE child_cancel,
                        wchar_t* block, DWORD capacity) {
    wchar_t windows[32768]{};
    wchar_t user_profile[32768]{};
    wchar_t cancel_text[32]{};
    UINT windows_length = GetWindowsDirectoryW(windows, ARRAYSIZE(windows));
    DWORD user_profile_length = kind == LaunchKind::LIFECYCLE_CLI
        ? GetEnvironmentVariableW(L"USERPROFILE", user_profile, ARRAYSIZE(user_profile))
        : 0;
    if (windows_length == 0 || windows_length >= ARRAYSIZE(windows) ||
        private_temp == nullptr || private_temp[0] == L'\0' ||
        (kind == LaunchKind::LIFECYCLE_CLI &&
         (user_profile_length == 0 || user_profile_length >= ARRAYSIZE(user_profile) ||
          !IsLocalAbsolutePath(user_profile)))) {
        return false;
    }
    DWORD position = 0;
    if (!AppendEnvironmentEntry(block, capacity, &position, L"SystemRoot", windows) ||
        !AppendEnvironmentEntry(block, capacity, &position, L"TEMP", private_temp) ||
        !AppendEnvironmentEntry(block, capacity, &position, L"TMP", private_temp)) return false;
    if (kind == LaunchKind::PRIVATE_WORKER &&
        (!UnsignedToDecimal(reinterpret_cast<uintptr_t>(child_cancel), cancel_text,
                            ARRAYSIZE(cancel_text)) ||
         !AppendEnvironmentEntry(block, capacity, &position,
                                 L"DAYZ_MCP_CANCEL_HANDLE", cancel_text))) return false;
    if (kind == LaunchKind::LIFECYCLE_CLI &&
        (!AppendEnvironmentEntry(block, capacity, &position,
                                 L"USERPROFILE", user_profile) ||
         !AppendEnvironmentEntry(block, capacity, &position,
                                 L"DAYZ_MCP_CLIENT_ID_JSON", secrets->identity) ||
         !AppendEnvironmentEntry(block, capacity, &position,
                                 L"DAYZ_MCP_LEASE_TOKEN", secrets->token) ||
         !AppendEnvironmentEntry(block, capacity, &position,
                                 L"DAYZ_MCP_NORMAL_POLICY_JSON",
                                 secrets->normal_policy))) return false;
    block[position++] = L'\0';
    SecureZero(windows, sizeof(windows));
    SecureZero(user_profile, sizeof(user_profile));
    SecureZero(cancel_text, sizeof(cancel_text));
    return true;
}

bool CancelRequested(HANDLE cancel_handle);

struct BoundedWrite {
    HANDLE output;
    const BYTE* bytes;
    DWORD size;
    BOOL result;
};

DWORD WINAPI BoundedWriteThread(LPVOID raw) {
    auto* write = static_cast<BoundedWrite*>(raw);
    DWORD offset = 0;
    while (offset < write->size) {
        DWORD written = 0;
        if (!WriteFile(write->output, write->bytes + offset,
                       write->size - offset, &written, nullptr) || written == 0) {
            write->result = FALSE;
            return 0;
        }
        offset += written;
    }
    write->result = TRUE;
    return 0;
}

bool WriteAllBounded(HANDLE output, const BYTE* bytes, DWORD size,
                     HANDLE guarded_process, HANDLE cancel_handle,
                     ULONGLONG deadline) {
    if (output == nullptr || output == INVALID_HANDLE_VALUE ||
        bytes == nullptr || size == 0) return false;
    BoundedWrite write{output, bytes, size, FALSE};
    HANDLE thread = CreateThread(nullptr, 0, BoundedWriteThread, &write, 0, nullptr);
    if (thread == nullptr) return false;
    for (;;) {
        DWORD state = WaitForSingleObject(thread, 10);
        if (state == WAIT_OBJECT_0) {
            CloseHandle(thread);
            return write.result == TRUE;
        }
        if (state == WAIT_FAILED || CancelRequested(cancel_handle) ||
            GetTickCount64() >= deadline) {
            if (guarded_process != nullptr && guarded_process != INVALID_HANDLE_VALUE)
                TerminateProcess(guarded_process, ERROR_TIMEOUT);
            CancelSynchronousIo(thread);
            if (WaitForSingleObject(thread, 5000) != WAIT_OBJECT_0)
                ExitProcess(ERROR_TIMEOUT);
            CloseHandle(thread);
            return false;
        }
    }
}

bool ValidateFrameMemory(const BYTE* frame, DWORD frame_bytes, BrokerHeader* header) {
    if (frame == nullptr || frame_bytes < sizeof(BrokerHeader)) return false;
    const auto* source = reinterpret_cast<const BrokerHeader*>(frame);
    *header = *source;
    uint64_t expected = static_cast<uint64_t>(sizeof(BrokerHeader)) +
                        header->payload_bytes + header->stdin_bytes;
    if (expected != frame_bytes || frame_bytes > kMaxFrameBytes ||
        header->magic[0] != kBrokerMagic[0] || header->magic[1] != kBrokerMagic[1] ||
        header->magic[2] != kBrokerMagic[2] || header->magic[3] != kBrokerMagic[3] ||
        header->version != kBrokerVersion || header->flags != 0 ||
        !IsApprovedKind(header->kind) || header->payload_bytes == 0) return false;
    BYTE digest[kHashBytes]{};
    bool ok = HashBuffer(frame + sizeof(BrokerHeader) + header->payload_bytes,
                         header->stdin_bytes, digest) &&
              SameBytes(digest, header->stdin_sha256, kHashBytes) &&
              ValidatePayloadByKind(*header, frame + sizeof(BrokerHeader));
    SecureZero(digest, sizeof(digest));
    return ok;
}

bool CancelRequested(HANDLE cancel_handle) {
    if (cancel_handle == nullptr || cancel_handle == INVALID_HANDLE_VALUE) return false;
    DWORD available = 0;
    if (PeekNamedPipe(cancel_handle, nullptr, 0, nullptr, &available, nullptr)) {
        return available != 0;
    }
    return true;
}

bool ReadWorkerMessage(HANDLE input, BYTE** frame, DWORD* frame_bytes) {
    *frame = nullptr;
    *frame_bytes = 0;
    DWORD size = 0;
    if (!ReadExact(input, reinterpret_cast<BYTE*>(&size), sizeof(size)) ||
        size < sizeof(kWorkerTerminalMagic) - 1 || size > kMaxFrameBytes) return false;
    BYTE* value = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, size));
    if (value == nullptr || !ReadExact(input, value, size)) {
        if (value != nullptr) HeapFree(GetProcessHeap(), 0, value);
        return false;
    }
    *frame = value;
    *frame_bytes = size;
    return true;
}

bool WriteWorkerResponse(HANDLE output, const BYTE* response, DWORD response_bytes,
                         HANDLE process, HANDLE cancel_handle) {
    return response != nullptr && response_bytes >= 2 && response_bytes <= kMaxFrameBytes &&
           WriteAllBounded(output, reinterpret_cast<const BYTE*>(&response_bytes),
                           sizeof(response_bytes), process, cancel_handle,
                           GetTickCount64() + 5000) &&
           WriteAllBounded(output, response, response_bytes, process, cancel_handle,
                           GetTickCount64() + 5000);
}

bool ReadChildOutput(HANDLE input, HANDLE process, HANDLE cancel_handle,
                     ULONGLONG deadline, BYTE** output, DWORD* output_bytes) {
    *output = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, kMaxFrameBytes));
    *output_bytes = 0;
    if (*output == nullptr) return false;
    for (;;) {
        if (CancelRequested(cancel_handle)) {
            TerminateProcess(process, ERROR_CANCELLED);
            return false;
        }
        if (GetTickCount64() >= deadline) {
            TerminateProcess(process, ERROR_TIMEOUT);
            return false;
        }
        DWORD available = 0;
        if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) {
            DWORD error = GetLastError();
            if ((error == ERROR_BROKEN_PIPE || error == ERROR_NO_DATA) &&
                WaitForSingleObject(process, 50) == WAIT_OBJECT_0) break;
            return false;
        }
        if (available != 0) {
            if (*output_bytes + available > kMaxFrameBytes) return false;
            DWORD read = 0;
            if (!ReadFile(input, *output + *output_bytes, available, &read, nullptr) || read == 0)
                return false;
            *output_bytes += read;
            continue;
        }
        if (WaitForSingleObject(process, 10) == WAIT_OBJECT_0) {
            if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr) || available == 0)
                break;
        }
    }
    return true;
}

bool ExpectAscii(const BYTE** cursor, const BYTE* end, const char* expected) {
    for (DWORD index = 0; expected[index] != '\0'; ++index) {
        if (*cursor >= end || **cursor != static_cast<BYTE>(expected[index])) return false;
        ++(*cursor);
    }
    return true;
}

bool ParseJsonBool(const BYTE** cursor, const BYTE* end, bool* value) {
    if (ExpectAscii(cursor, end, "true")) { *value = true; return true; }
    if (ExpectAscii(cursor, end, "false")) { *value = false; return true; }
    return false;
}

bool ParseJsonString(const BYTE** cursor, const BYTE* end, wchar_t* output, DWORD capacity) {
    if (*cursor >= end || *(*cursor)++ != '"') return false;
    BYTE utf8[4096]{};
    DWORD used = 0;
    bool closed = false;
    while (*cursor < end) {
        BYTE value = *(*cursor)++;
        if (value == '"') { closed = true; break; }
        if (value < 0x20 || used + 1 >= ARRAYSIZE(utf8)) return false;
        if (value == '\\') {
            if (*cursor >= end) return false;
            BYTE escaped = *(*cursor)++;
            if (escaped == '"' || escaped == '\\' || escaped == '/') value = escaped;
            else if (escaped == 'b') value = '\b';
            else if (escaped == 'f') value = '\f';
            else if (escaped == 'n') value = '\n';
            else if (escaped == 'r') value = '\r';
            else if (escaped == 't') value = '\t';
            else return false;
        }
        utf8[used++] = value;
    }
    if (!closed) return false;
    int converted = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                        reinterpret_cast<const char*>(utf8), used,
                                        output, capacity - 1);
    SecureZero(utf8, sizeof(utf8));
    if (converted <= 0 || static_cast<DWORD>(converted) >= capacity) return false;
    output[converted] = L'\0';
    return true;
}

bool IsValidPrefix(const wchar_t* value) {
    DWORD length = 0;
    while (value[length] != L'\0') {
        wchar_t character = value[length];
        bool alpha = (character >= L'A' && character <= L'Z') ||
                     (character >= L'a' && character <= L'z');
        bool digit = character >= L'0' && character <= L'9';
        if ((!alpha && (length == 0 || (!digit && character != L'_'))) || length >= 64)
            return false;
        ++length;
    }
    return length != 0;
}

bool IsLocalAbsolutePath(const wchar_t* value) {
    bool alpha = (value[0] >= L'A' && value[0] <= L'Z') ||
                 (value[0] >= L'a' && value[0] <= L'z');
    if (!alpha || value[1] != L':' || value[2] != L'\\') return false;
    for (DWORD index = 3; value[index] != L'\0'; ++index) {
        if (value[index] < L' ' || value[index] == L'"' || value[index] == L':' ||
            value[index] == L'/' || value[index] == L'<' || value[index] == L'>' ||
            value[index] == L'|' || value[index] == L'*' || value[index] == L'?') return false;
    }
    return true;
}

bool ExpectedAddonPath(wchar_t* destination, DWORD capacity, const wchar_t* root,
                       const wchar_t* prefix, const wchar_t* suffix = L"") {
    return CopyText(destination, capacity, root) &&
           AppendText(destination, capacity, prefix) &&
           AppendText(destination, capacity, suffix);
}

const AddonRootEntry* FindAddonRoots(const wchar_t* prefix) {
    for (DWORD index = 0; index < kAddonRootCount; ++index) {
        if (SamePathText(kAddonRoots[index].prefix, prefix)) {
            return &kAddonRoots[index];
        }
    }
    return nullptr;
}

bool ParseAddonRequest(const BYTE* payload, DWORD bytes, AddonRequest* request) {
    const BYTE* cursor = payload;
    const BYTE* end = payload + bytes;
    return ExpectAscii(&cursor, end, "{\"clear\":") &&
           ParseJsonBool(&cursor, end, &request->clear) &&
           ExpectAscii(&cursor, end, ",\"pack_only\":") &&
           ParseJsonBool(&cursor, end, &request->pack_only) &&
           ExpectAscii(&cursor, end, ",\"prefix\":") &&
           ParseJsonString(&cursor, end, request->prefix, ARRAYSIZE(request->prefix)) &&
           ExpectAscii(&cursor, end, ",\"source\":") &&
           ParseJsonString(&cursor, end, request->source, ARRAYSIZE(request->source)) &&
           ExpectAscii(&cursor, end, ",\"target\":") &&
           ParseJsonString(&cursor, end, request->target, ARRAYSIZE(request->target)) &&
           ExpectAscii(&cursor, end, ",\"temp\":") &&
           ParseJsonString(&cursor, end, request->temp, ARRAYSIZE(request->temp)) &&
           ExpectAscii(&cursor, end, "}") && cursor == end && [&]() {
               wchar_t expected_target[521]{};
               wchar_t expected_temp[521]{};
               const AddonRootEntry* roots = FindAddonRoots(request->prefix);
               return roots != nullptr &&
                      IsValidPrefix(request->prefix) && IsLocalAbsolutePath(request->source) &&
                      ExpectedAddonPath(expected_target, ARRAYSIZE(expected_target),
                                        roots->target_root, request->prefix, L"\\Addons") &&
                      ExpectedAddonPath(expected_temp, ARRAYSIZE(expected_temp),
                                        roots->temp_root, request->prefix) &&
                      SamePathText(request->target, expected_target) &&
                      SamePathText(request->temp, expected_temp);
           }();
}

BYTE LowerHexDigit(BYTE value) {
    return value < 10 ? static_cast<BYTE>('0' + value)
                      : static_cast<BYTE>('a' + value - 10);
}

bool ValidatePrivatePayload(const BrokerHeader& header, const BYTE* payload) {
    const BYTE* cursor = payload;
    const BYTE* end = payload + header.payload_bytes;
    if (header.stdin_bytes == 0 ||
        !ExpectAscii(&cursor, end, "{\"request_sha256\":\"")) return false;
    for (DWORD index = 0; index < kHashBytes; ++index) {
        if (cursor + 2 > end || cursor[0] != LowerHexDigit(header.stdin_sha256[index] >> 4) ||
            cursor[1] != LowerHexDigit(header.stdin_sha256[index] & 0x0F)) return false;
        cursor += 2;
    }
    return ExpectAscii(&cursor, end, "\"}") && cursor == end;
}

bool IsLowerHex(BYTE value) {
    return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f');
}

bool ParseCanonicalUuidOrNull(const BYTE** cursor, const BYTE* end, bool* present) {
    const BYTE* start = *cursor;
    if (ExpectAscii(cursor, end, "null")) {
        *present = false;
        return true;
    }
    *cursor = start;
    if (end - *cursor < 38 || *(*cursor)++ != '"') return false;
    for (DWORD index = 0; index < 36; ++index) {
        BYTE value = *(*cursor)++;
        bool hyphen = index == 8 || index == 13 || index == 18 || index == 23;
        if ((hyphen && value != '-') || (!hyphen && !IsLowerHex(value))) return false;
        if (index == 14 && value != '4') return false;
        if (index == 19 && value != '8' && value != '9' && value != 'a' && value != 'b')
            return false;
    }
    if (*(*cursor)++ != '"') return false;
    *present = true;
    return true;
}

enum class LifecycleCommand : BYTE {
    INVALID = 0,
    START,
    STOP,
    ADOPT,
    REAP,
    ACK,
    STATUS,
};

bool MatchAscii(const BYTE** cursor, const BYTE* end, const char* expected) {
    const BYTE* candidate = *cursor;
    if (!ExpectAscii(&candidate, end, expected)) return false;
    *cursor = candidate;
    return true;
}

LifecycleCommand ParseLifecycleCommand(const BYTE** cursor, const BYTE* end) {
    struct Candidate { const char* text; LifecycleCommand command; };
    static const Candidate candidates[] = {
        {"\"start\"", LifecycleCommand::START},
        {"\"stop\"", LifecycleCommand::STOP},
        {"\"adopt\"", LifecycleCommand::ADOPT},
        {"\"reap\"", LifecycleCommand::REAP},
        {"\"ack\"", LifecycleCommand::ACK},
        {"\"status\"", LifecycleCommand::STATUS},
    };
    for (DWORD index = 0; index < ARRAYSIZE(candidates); ++index)
        if (MatchAscii(cursor, end, candidates[index].text)) return candidates[index].command;
    return LifecycleCommand::INVALID;
}

bool ValidateLifecyclePayload(const BrokerHeader& header, const BYTE* payload,
                              LifecycleCommand* parsed_command = nullptr) {
    const BYTE* cursor = payload;
    const BYTE* end = payload + header.payload_bytes;
    if (!ExpectAscii(&cursor, end, "{\"command\":")) return false;
    LifecycleCommand command = ParseLifecycleCommand(&cursor, end);
    bool has_operation = false;
    bool has_run = false;
    if (command == LifecycleCommand::INVALID ||
        !ExpectAscii(&cursor, end, ",\"launch_operation_id\":") ||
        !ParseCanonicalUuidOrNull(&cursor, end, &has_operation) ||
        !ExpectAscii(&cursor, end, ",\"run_id\":") ||
        !ParseCanonicalUuidOrNull(&cursor, end, &has_run) ||
        !ExpectAscii(&cursor, end, "}") || cursor != end) return false;
    if (parsed_command != nullptr) *parsed_command = command;
    if (command == LifecycleCommand::START)
        return has_run && header.stdin_bytes != 0;
    if (command == LifecycleCommand::STOP || command == LifecycleCommand::ADOPT ||
        command == LifecycleCommand::REAP)
        return has_run && !has_operation && header.stdin_bytes == 0;
    if (command == LifecycleCommand::ACK)
        return has_run && has_operation && header.stdin_bytes == 0;
    return command == LifecycleCommand::STATUS && !has_operation &&
           header.stdin_bytes == 0;
}

bool ValidatePayloadByKind(const BrokerHeader& header, const BYTE* payload) {
    LaunchKind kind = static_cast<LaunchKind>(header.kind);
    if (kind == LaunchKind::PRIVATE_WORKER) return ValidatePrivatePayload(header, payload);
    if (kind == LaunchKind::LIFECYCLE_CLI) return ValidateLifecyclePayload(header, payload);
    if (kind == LaunchKind::ADDON_BUILDER) {
        AddonRequest request{};
        return header.stdin_bytes == 0 &&
               ParseAddonRequest(payload, header.payload_bytes, &request);
    }
    return false;
}

bool AppendQuoted(wchar_t* command, DWORD capacity, const wchar_t* value) {
    for (DWORD index = 0; value[index] != L'\0'; ++index)
        if (value[index] == L'"' || value[index] < L' ') return false;
    return AppendText(command, capacity, L" \"") && AppendText(command, capacity, value) &&
           AppendText(command, capacity, L"\"");
}

bool BuildAddonCommand(const AddonRequest& request, wchar_t* application, DWORD app_capacity,
                       wchar_t* command, DWORD command_capacity) {
    static const wchar_t* addon =
        L"C:\\Program Files (x86)\\Steam\\steamapps\\common\\DayZ Tools\\Bin\\AddonBuilder\\AddonBuilder.exe";
    return CopyText(application, app_capacity, addon) &&
           CopyText(command, command_capacity, L"\"") &&
           AppendText(command, command_capacity, addon) &&
           AppendText(command, command_capacity, L"\"") &&
           AppendQuoted(command, command_capacity, request.source) &&
           AppendQuoted(command, command_capacity, request.target) &&
           AppendText(command, command_capacity, L" -prefix=") &&
           AppendText(command, command_capacity, request.prefix) &&
           AppendText(command, command_capacity, L" \"-temp=") &&
           AppendText(command, command_capacity, request.temp) &&
           AppendText(command, command_capacity, L"\"") &&
           (!request.clear || AppendText(command, command_capacity, L" -clear")) &&
           (!request.pack_only || AppendText(command, command_capacity, L" -packonly"));
}

bool BuildPboPath(const AddonRequest& request, wchar_t* destination, DWORD capacity) {
    return CopyText(destination, capacity, request.target) &&
           AppendText(destination, capacity, L"\\") &&
           AppendText(destination, capacity, request.prefix) &&
           AppendText(destination, capacity, L".pbo");
}

bool CapturePboSnapshot(const wchar_t* path, bool allow_missing, PboSnapshot* snapshot) {
    if (path == nullptr || snapshot == nullptr) return false;
    SecureZero(snapshot, sizeof(*snapshot));
    HANDLE file = CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        DWORD error = GetLastError();
        return allow_missing &&
               (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND);
    }
    FILE_ATTRIBUTE_TAG_INFO tag{};
    bool valid = GetFileType(file) == FILE_TYPE_DISK &&
        GetFileInformationByHandleEx(
            file, FileIdInfo, &snapshot->identity, sizeof(snapshot->identity)) &&
        GetFileInformationByHandleEx(
            file, FileBasicInfo, &snapshot->basic, sizeof(snapshot->basic)) &&
        GetFileInformationByHandleEx(
            file, FileStandardInfo, &snapshot->standard, sizeof(snapshot->standard)) &&
        GetFileInformationByHandleEx(file, FileAttributeTagInfo, &tag, sizeof(tag)) &&
        (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0 &&
        !snapshot->standard.Directory && !snapshot->standard.DeletePending &&
        snapshot->standard.NumberOfLinks == 1 &&
        snapshot->standard.EndOfFile.QuadPart > 0 &&
        HashHandle(file, snapshot->sha256);
    CloseHandle(file);
    SecureZero(&tag, sizeof(tag));
    if (!valid) {
        SecureZero(snapshot, sizeof(*snapshot));
        return false;
    }
    snapshot->exists = true;
    return true;
}

bool PboSnapshotChanged(const PboSnapshot& before, const PboSnapshot& after) {
    if (!after.exists) return false;
    if (!before.exists) return true;
    return before.identity.VolumeSerialNumber != after.identity.VolumeSerialNumber ||
           !SameBytes(before.identity.FileId.Identifier, after.identity.FileId.Identifier, 16) ||
           before.basic.LastWriteTime.QuadPart != after.basic.LastWriteTime.QuadPart ||
           before.basic.ChangeTime.QuadPart != after.basic.ChangeTime.QuadPart ||
           before.standard.EndOfFile.QuadPart != after.standard.EndOfFile.QuadPart ||
           !SameBytes(before.sha256, after.sha256, kHashBytes);
}

void FreeChildOutput(ChildOutput* output) {
    if (output->bytes != nullptr) {
        SecureZero(output->bytes, output->size);
        HeapFree(GetProcessHeap(), 0, output->bytes);
    }
    SecureZero(output, sizeof(*output));
}

bool AddonResponse(const wchar_t* pbo_path, const PboSnapshot& before,
                   DWORD exit_code, ChildOutput* output) {
    PboSnapshot after{};
    bool valid = exit_code == 0 && CapturePboSnapshot(pbo_path, false, &after) &&
                 PboSnapshotChanged(before, after);
    char number[32]{};
    uint64_t value = valid ? static_cast<uint64_t>(after.standard.EndOfFile.QuadPart) : 0;
    SecureZero(&after, sizeof(after));
    DWORD digits = 0;
    do { number[digits++] = static_cast<char>('0' + value % 10); value /= 10; } while (value != 0);
    for (DWORD left = 0, right = digits - 1; left < right; ++left, --right) {
        char swap = number[left]; number[left] = number[right]; number[right] = swap;
    }
    const char* prefix = valid ? "{\"exit_code\":0,\"ok\":true,\"pbo_size\":" :
                                 "{\"exit_code\":1,\"ok\":false,\"pbo_size\":";
    DWORD prefix_bytes = 0; while (prefix[prefix_bytes] != '\0') ++prefix_bytes;
    output->size = prefix_bytes + digits + 1;
    output->bytes = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, output->size));
    if (output->bytes == nullptr) return false;
    for (DWORD index = 0; index < prefix_bytes; ++index) output->bytes[index] = prefix[index];
    for (DWORD index = 0; index < digits; ++index) output->bytes[prefix_bytes + index] = number[index];
    output->bytes[output->size - 1] = '}';
    output->exit_code = valid ? 0 : 1;
    return valid;
}

bool ResponseLooksClosed(const BYTE* response, DWORD bytes) {
    if (response == nullptr || bytes < 2 || bytes > kMaxFrameBytes ||
        response[0] != '{' || response[bytes - 1] != '}') return false;
    for (DWORD index = 0; index < bytes; ++index)
        if (response[index] == 0) return false;
    return true;
}

bool PublishAnnouncement(LaunchKind kind, const wchar_t* manifest_path) {
    const ClosureEntry* entry = nullptr;
    for (DWORD index = 0; index < kClosureEntryCount; ++index) {
        if (SameBundlePathText(kClosureEntries[index].path, manifest_path)) {
            entry = &kClosureEntries[index];
            break;
        }
    }
    if (entry == nullptr) return false;
    wchar_t image_path[32768]{};
    bool path_ok = entry->kind == ClosureKind::BUNDLE
        ? BundlePath(entry->path, image_path, ARRAYSIZE(image_path))
        : entry->kind == ClosureKind::EXTERNAL &&
              CopyText(image_path, ARRAYSIZE(image_path), entry->path);
    if (!path_ok) return false;
    HANDLE image = CreateFileW(image_path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                               OPEN_EXISTING,
                               FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
                               nullptr);
    FILE_ID_INFO identity{};
    FILE_ATTRIBUTE_TAG_INFO tag{};
    bool identity_ok = image != INVALID_HANDLE_VALUE &&
        GetFileInformationByHandleEx(image, FileIdInfo, &identity, sizeof(identity)) &&
        GetFileInformationByHandleEx(image, FileAttributeTagInfo, &tag, sizeof(tag)) &&
        (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
    if (image != INVALID_HANDLE_VALUE) CloseHandle(image);
    SecureZero(image_path, sizeof(image_path));
    if (!identity_ok) {
        SecureZero(&identity, sizeof(identity));
        return false;
    }
    char path[2048]{};
    int converted = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, manifest_path, -1,
                                        path, ARRAYSIZE(path), nullptr, nullptr);
    if (converted <= 1 || converted > static_cast<int>(ARRAYSIZE(path))) return false;
    BrokerAnnouncement announcement{{
        static_cast<BYTE>(kAnnouncementMagic[0]), static_cast<BYTE>(kAnnouncementMagic[1]),
        static_cast<BYTE>(kAnnouncementMagic[2]), static_cast<BYTE>(kAnnouncementMagic[3])},
                                    1, static_cast<BYTE>(kind), 0,
                                    static_cast<uint32_t>(InterlockedIncrement(&gAnnouncementSequence)),
                                    static_cast<uint32_t>(converted - 1), {},
                                    identity.VolumeSerialNumber, {}};
    for (DWORD index = 0; index < 32; ++index) announcement.image_sha256[index] = entry->sha256[index];
    for (DWORD index = 0; index < 16; ++index)
        announcement.file_id[index] = identity.FileId.Identifier[index];
    HANDLE stream = GetStdHandle(STD_ERROR_HANDLE);
    bool ok = stream != nullptr && stream != INVALID_HANDLE_VALUE &&
              WriteAllBounded(stream, reinterpret_cast<const BYTE*>(&announcement),
                              sizeof(announcement), nullptr, nullptr,
                              GetTickCount64() + 5000) &&
              WriteAllBounded(stream, reinterpret_cast<const BYTE*>(path), converted - 1,
                              nullptr, nullptr, GetTickCount64() + 5000);
    SecureZero(&identity, sizeof(identity));
    SecureZero(path, sizeof(path));
    return ok;
}

BOOL LaunchApprovedChild(LaunchKind kind, const BYTE* broker_frame,
                         DWORD broker_frame_bytes, const SecretState* secrets,
                         HANDLE cancel_handle, HANDLE private_cancel_handle,
                         ChildOutput* output) {
    if (output == nullptr || secrets == nullptr ||
        (kind != LaunchKind::PRIVATE_WORKER && kind != LaunchKind::LIFECYCLE_CLI &&
         kind != LaunchKind::ADDON_BUILDER)) {
        return FALSE;
    }
    output->bytes = nullptr; output->size = 0; output->exit_code = ERROR_GEN_FAILURE;
    BrokerHeader frame_header{};
    if (!ValidateFrameMemory(broker_frame, broker_frame_bytes, &frame_header) ||
        frame_header.kind != static_cast<BYTE>(kind)) return FALSE;
    wchar_t application[32768]{};
    wchar_t command[32768]{};
    wchar_t app_archive[32768]{};
    const wchar_t* manifest_path = L"runtime\\python.exe";
    AddonRequest addon{};
    wchar_t pbo_path[1024]{};
    PboSnapshot pbo_before{};
    if (kind == LaunchKind::ADDON_BUILDER) {
        manifest_path = L"C:\\Program Files (x86)\\Steam\\steamapps\\common\\DayZ Tools\\Bin\\AddonBuilder\\AddonBuilder.exe";
        if (frame_header.stdin_bytes != 0 ||
            !ParseAddonRequest(broker_frame + sizeof(BrokerHeader), frame_header.payload_bytes, &addon) ||
            !BuildAddonCommand(addon, application, ARRAYSIZE(application), command, ARRAYSIZE(command)) ||
            !BuildPboPath(addon, pbo_path, ARRAYSIZE(pbo_path)) ||
            !CapturePboSnapshot(pbo_path, true, &pbo_before))
            return FALSE;
    } else if (!BundlePath(L"runtime\\python.exe", application, ARRAYSIZE(application)) ||
        !BundlePath(L"app.pyz", app_archive, ARRAYSIZE(app_archive)) ||
        !CopyText(command, ARRAYSIZE(command), L"\"") ||
        !AppendText(command, ARRAYSIZE(command), application) ||
        !AppendText(command, ARRAYSIZE(command), L"\" -I -B -S \"") ||
        !AppendText(command, ARRAYSIZE(command), app_archive) ||
        !AppendText(command, ARRAYSIZE(command), L"\"")) {
        return FALSE;
    }

    if (!PublishAnnouncement(kind, manifest_path)) return FALSE;
    if (kind == LaunchKind::LIFECYCLE_CLI &&
        !AppendText(command, ARRAYSIZE(command), L" --lifecycle-child")) {
        return FALSE;
    }

    SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
    HANDLE child_cancel = nullptr;
    HANDLE child_input = nullptr;
    HANDLE parent_input = nullptr;
    HANDLE child_output = nullptr;
    HANDLE parent_output = nullptr;
    HANDLE null_error = INVALID_HANDLE_VALUE;
    if (kind == LaunchKind::PRIVATE_WORKER &&
        (cancel_handle == nullptr || cancel_handle == INVALID_HANDLE_VALUE ||
         private_cancel_handle == nullptr ||
         private_cancel_handle == INVALID_HANDLE_VALUE ||
         cancel_handle == private_cancel_handle ||
         GetFileType(cancel_handle) != FILE_TYPE_PIPE ||
         GetFileType(private_cancel_handle) != FILE_TYPE_PIPE ||
         !DuplicateHandle(GetCurrentProcess(), private_cancel_handle, GetCurrentProcess(),
                          &child_cancel, 0, TRUE, DUPLICATE_SAME_ACCESS))) {
        return FALSE;
    }
    if (!CreatePipe(&child_input, &parent_input, &security, 0) ||
        !SetHandleInformation(parent_input, HANDLE_FLAG_INHERIT, 0) ||
        !CreatePipe(&parent_output, &child_output, &security, 0) ||
        !SetHandleInformation(parent_output, HANDLE_FLAG_INHERIT, 0)) {
        if (child_input != nullptr) CloseHandle(child_input);
        if (parent_input != nullptr) CloseHandle(parent_input);
        if (child_output != nullptr) CloseHandle(child_output);
        if (parent_output != nullptr) CloseHandle(parent_output);
        if (child_cancel != nullptr) CloseHandle(child_cancel);
        return FALSE;
    }
    null_error = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              &security, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (null_error == INVALID_HANDLE_VALUE) {
        CloseHandle(child_input);
        CloseHandle(parent_input);
        CloseHandle(child_output);
        CloseHandle(parent_output);
        if (child_cancel != nullptr) CloseHandle(child_cancel);
        return FALSE;
    }
    SIZE_T attribute_bytes = 0;
    DWORD attribute_count = kind == LaunchKind::ADDON_BUILDER ? 2 : 3;
    InitializeProcThreadAttributeList(nullptr, attribute_count, 0, &attribute_bytes);
    auto* attributes = static_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(
        HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, attribute_bytes));
    HANDLE inherited[] = {child_input, child_output, null_error, child_cancel};
    SIZE_T inherited_bytes = kind == LaunchKind::PRIVATE_WORKER
        ? sizeof(inherited)
        : sizeof(HANDLE) * 3;
    STARTUPINFOEXW startup{};
    PROCESS_INFORMATION process{};
    HANDLE child_job = CreateJobObjectW(nullptr, nullptr);
    HANDLE child_jobs[] = {child_job};
    DWORD child_policy = PROCESS_CREATION_CHILD_PROCESS_RESTRICTED;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION job_limits{};
    job_limits.BasicLimitInformation.LimitFlags =
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
    job_limits.BasicLimitInformation.ActiveProcessLimit =
        kind == LaunchKind::ADDON_BUILDER ? 2 : 1;
    wchar_t environment[32768]{};
    wchar_t cwd[32768]{};
    DWORD cwd_length = GetCurrentDirectoryW(ARRAYSIZE(cwd), cwd);
    BOOL created = FALSE;
    bool configured = child_job != nullptr &&
        SetInformationJobObject(child_job, JobObjectExtendedLimitInformation,
                                &job_limits, sizeof(job_limits)) &&
        cwd_length != 0 && cwd_length < ARRAYSIZE(cwd) &&
        attributes != nullptr &&
        InitializeProcThreadAttributeList(attributes, attribute_count, 0, &attribute_bytes) &&
        UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                  inherited, inherited_bytes, nullptr, nullptr) &&
        UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
                                  child_jobs, sizeof(child_jobs), nullptr, nullptr) &&
        (kind == LaunchKind::ADDON_BUILDER ||
         UpdateProcThreadAttribute(attributes, 0,
                                   PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY,
                                   &child_policy, sizeof(child_policy), nullptr, nullptr)) &&
        MinimalEnvironment(kind, secrets, cwd, child_cancel,
                           environment, ARRAYSIZE(environment));
    if (configured) {
        startup.StartupInfo.cb = sizeof(startup);
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = child_input;
        startup.StartupInfo.hStdOutput = child_output;
        startup.StartupInfo.hStdError = null_error;
        startup.lpAttributeList = attributes;
        created = CreateProcessW(
            application, command, nullptr, nullptr, TRUE,
            CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED,
            environment, cwd, &startup.StartupInfo, &process);
    }
    SecureZero(environment, sizeof(environment));
    SecureZero(cwd, sizeof(cwd));
    CloseHandle(child_input);
    CloseHandle(child_output);
    CloseHandle(null_error);
    if (child_cancel != nullptr) CloseHandle(child_cancel);
    if (attributes != nullptr) {
        DeleteProcThreadAttributeList(attributes);
        HeapFree(GetProcessHeap(), 0, attributes);
    }
    if (!created) {
        CloseHandle(parent_input);
        CloseHandle(parent_output);
        SecureZero(application, sizeof(application));
        SecureZero(command, sizeof(command));
        if (child_job != nullptr) CloseHandle(child_job);
        return FALSE;
    }
    if (ResumeThread(process.hThread) == static_cast<DWORD>(-1)) {
        TerminateProcess(process.hProcess, ERROR_ACCESS_DENIED);
        CloseHandle(parent_input);
        CloseHandle(parent_output);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        CloseHandle(child_job);
        return FALSE;
    }
    bool write_ok = WriteAllBounded(
        parent_input, broker_frame, broker_frame_bytes, process.hProcess,
        cancel_handle, GetTickCount64() + 5000);
    if (kind != LaunchKind::PRIVATE_WORKER) CloseHandle(parent_input);
    if (!write_ok) {
        TerminateProcess(process.hProcess, ERROR_INVALID_DATA);
    }
    bool broker_ok = write_ok;
    if (kind == LaunchKind::PRIVATE_WORKER && broker_ok) {
        ULONGLONG cancel_deadline = 0;
        for (;;) {
            if (CancelRequested(cancel_handle)) {
                if (cancel_deadline == 0) cancel_deadline = GetTickCount64() + 20000;
                if (GetTickCount64() >= cancel_deadline) {
                    TerminateProcess(process.hProcess, ERROR_TIMEOUT);
                    broker_ok = false;
                    break;
                }
            }
            BYTE* request = nullptr; DWORD request_bytes = 0;
            if (!ReadWorkerMessage(parent_output, &request, &request_bytes)) {
                broker_ok = false; break;
            }
            if (cancel_deadline == 0 && CancelRequested(cancel_handle))
                cancel_deadline = GetTickCount64() + 20000;
            if (request_bytes >= 4 && request[0] == kWorkerTerminalMagic[0] &&
                request[1] == kWorkerTerminalMagic[1] &&
                request[2] == kWorkerTerminalMagic[2] &&
                request[3] == kWorkerTerminalMagic[3]) {
                output->size = request_bytes - 4;
                output->bytes = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), 0, output->size));
                if (output->bytes == nullptr) broker_ok = false;
                else for (DWORD index = 0; index < output->size; ++index)
                    output->bytes[index] = request[index + 4];
                SecureZero(request, request_bytes); HeapFree(GetProcessHeap(), 0, request);
                break;
            }
            BrokerHeader child_header{};
            ChildOutput child_result{};
            LifecycleCommand lifecycle_command = LifecycleCommand::INVALID;
            bool frame_ok = ValidateFrameMemory(request, request_bytes, &child_header) &&
                child_header.kind != static_cast<BYTE>(LaunchKind::PRIVATE_WORKER);
            bool cleanup_only = frame_ok && cancel_deadline != 0 &&
                child_header.kind == static_cast<BYTE>(LaunchKind::LIFECYCLE_CLI) &&
                ValidateLifecyclePayload(
                    child_header,
                    request + sizeof(BrokerHeader),
                    &lifecycle_command) &&
                lifecycle_command == LifecycleCommand::STOP;
            bool child_ok = frame_ok && (cancel_deadline == 0 || cleanup_only) &&
                LaunchApprovedChild(static_cast<LaunchKind>(child_header.kind), request,
                                    request_bytes, secrets,
                                    cleanup_only ? nullptr : cancel_handle,
                                    nullptr,
                                    &child_result);
            static const BYTE failure[] = "{\"error\":\"broker_child_failed\",\"ok\":false}";
            const BYTE* response = child_ok ? child_result.bytes : failure;
            DWORD response_bytes = child_ok ? child_result.size : sizeof(failure) - 1;
            if (!WriteWorkerResponse(
                    parent_input, response, response_bytes, process.hProcess,
                    cancel_deadline == 0 ? cancel_handle : nullptr)) broker_ok = false;
            FreeChildOutput(&child_result);
            SecureZero(request, request_bytes); HeapFree(GetProcessHeap(), 0, request);
            if (!broker_ok) break;
        }
        CloseHandle(parent_input);
    } else if (broker_ok) {
        ULONGLONG child_deadline = GetTickCount64() +
            (kind == LaunchKind::ADDON_BUILDER ? 2ULL * 60 * 60 * 1000 : 20000);
        broker_ok = ReadChildOutput(parent_output, process.hProcess, cancel_handle,
                                    child_deadline, &output->bytes, &output->size);
    }
    bool exited = false;
    for (DWORD waited = 0; waited < 5000; waited += 10) {
        if (WaitForSingleObject(process.hProcess, 10) == WAIT_OBJECT_0) {
            exited = true;
            break;
        }
        if (kind != LaunchKind::PRIVATE_WORKER && CancelRequested(cancel_handle)) {
            TerminateProcess(process.hProcess, ERROR_CANCELLED);
            broker_ok = false;
        }
    }
    if (!exited) {
        TerminateProcess(process.hProcess, ERROR_TIMEOUT);
        exited = WaitForSingleObject(process.hProcess, 5000) == WAIT_OBJECT_0;
        broker_ok = false;
    }
    DWORD code = ERROR_GEN_FAILURE;
    GetExitCodeProcess(process.hProcess, &code);
    output->exit_code = code;
    CloseHandle(parent_output);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    CloseHandle(child_job);
    SecureZero(application, sizeof(application));
    SecureZero(command, sizeof(command));
    SecureZero(app_archive, sizeof(app_archive));
    if (kind == LaunchKind::ADDON_BUILDER) {
        FreeChildOutput(output);
        broker_ok = AddonResponse(pbo_path, pbo_before, code, output);
        SecureZero(pbo_path, sizeof(pbo_path));
        SecureZero(&pbo_before, sizeof(pbo_before));
    } else if (kind == LaunchKind::LIFECYCLE_CLI &&
               !ResponseLooksClosed(output->bytes, output->size)) {
        broker_ok = false;
    } else if (kind == LaunchKind::PRIVATE_WORKER && output->size == 0) {
        broker_ok = false;
    }
    return broker_ok ? TRUE : FALSE;
}

}  // namespace

extern "C" void __cdecl __security_init_cookie();

HANDLE TakePipeHandle(const wchar_t* name) {
    wchar_t text[32]{};
    DWORD characters = GetEnvironmentVariableW(name, text, ARRAYSIZE(text));
    SetEnvironmentVariableW(name, nullptr);
    if (characters == 0 || characters >= ARRAYSIZE(text)) return nullptr;
    UINT_PTR value = 0;
    for (DWORD index = 0; index < characters; ++index) {
        if (text[index] < L'0' || text[index] > L'9') return nullptr;
        UINT_PTR digit = static_cast<UINT_PTR>(text[index] - L'0');
        if (value > (static_cast<UINT_PTR>(-1) - digit) / 10) return nullptr;
        value = value * 10 + digit;
    }
    SecureZero(text, sizeof(text));
    return reinterpret_cast<HANDLE>(value);
}

extern "C" void __cdecl wWinMainCRTStartup() {
    __security_init_cookie();
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_APPLICATION_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    SecretState secrets{};
    PinnedClosure closure{};
    BrokerHeader header{};
    BYTE* body = nullptr;
    ChildOutput output{};
    BOOL launched = FALSE;
    DWORD failure_code = ERROR_INVALID_DATA;
    HANDLE cancel_handle = TakePipeHandle(L"DAYZ_MCP_CANCEL_HANDLE");
    HANDLE worker_cancel_handle =
        TakePipeHandle(L"DAYZ_MCP_WORKER_CANCEL_HANDLE");
    bool secrets_ok = CaptureSecrets(&secrets);
    bool closure_ok = false;
    bool frame_ok = false;
    if (!secrets_ok) {
        failure_code = ERROR_ENVVAR_NOT_FOUND;
    } else if (!(closure_ok = ValidateClosure(&closure))) {
        failure_code = ERROR_CRC;
    } else if (!(frame_ok = ReadBrokerFrame(&header, &body))) {
        failure_code = ERROR_INVALID_DATA;
    } else if (header.kind != static_cast<BYTE>(LaunchKind::PRIVATE_WORKER)) {
        failure_code = ERROR_INVALID_PARAMETER;
    } else {
        DWORD frame_bytes = sizeof(BrokerHeader) + header.payload_bytes + header.stdin_bytes;
        BYTE* frame = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, frame_bytes));
        if (frame != nullptr) {
            for (DWORD index = 0; index < sizeof(BrokerHeader); ++index)
                frame[index] = reinterpret_cast<BYTE*>(&header)[index];
            for (DWORD index = 0; index < header.payload_bytes + header.stdin_bytes; ++index)
                frame[sizeof(BrokerHeader) + index] = body[index];
            launched = LaunchApprovedChild(LaunchKind::PRIVATE_WORKER, frame, frame_bytes,
                                           &secrets, cancel_handle,
                                           worker_cancel_handle, &output);
            if (!launched) failure_code = ERROR_GEN_FAILURE;
            SecureZero(frame, frame_bytes); HeapFree(GetProcessHeap(), 0, frame);
        } else {
            failure_code = ERROR_NOT_ENOUGH_MEMORY;
        }
    }
    if (launched && output.bytes != nullptr && output.size <= 4096)
        launched = WriteAllBounded(
            GetStdHandle(STD_OUTPUT_HANDLE), output.bytes, output.size,
            nullptr, nullptr, GetTickCount64() + 5000);
    DWORD exit_code = launched ? output.exit_code : failure_code;
    FreeChildOutput(&output);
    if (body != nullptr) {
        SecureZero(body, header.payload_bytes + header.stdin_bytes);
        HeapFree(GetProcessHeap(), 0, body);
    }
    ClosePinned(&closure);
    DestroySecrets(&secrets);
    ExitProcess(exit_code);
}
