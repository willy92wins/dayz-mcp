#pragma once
#include <windows.h>

struct AddonRootEntry {
    const wchar_t* prefix;
    const wchar_t* target_root;
    const wchar_t* temp_root;
};

inline constexpr AddonRootEntry kAddonRoots[] = {
    {L"LFHeli_OH1", L"C:\\mods\\@", L"C:\\temp\\"},
    {L"SimpleGroup", L"C:\\mods\\@", L"C:\\temp\\"},
};

inline constexpr DWORD kAddonRootCount = ARRAYSIZE(kAddonRoots);
