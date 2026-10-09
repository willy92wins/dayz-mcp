// Offline driver for launcher.cpp. It never calls the sealed entry point.
#include <stdio.h>
#include <windows.h>

struct DzmcpTestApi {
    int (*validate)();
    int (*include_open)();
    int (*close_include)();
    int (*ns)(const wchar_t* source, const wchar_t* prefix, wchar_t* out, int out_cap, int* handle_open);
    int (*close_ns)();
    int (*command)(
        int clear, int pack_only, const wchar_t* prefix, const wchar_t* source,
        const wchar_t* target, const wchar_t* temp, const wchar_t* addon,
        wchar_t* command, int command_cap, wchar_t* pbo, int pbo_cap,
        int* include_open, int* prefix_open);
};

extern DzmcpTestApi g_dzmcp_test_api;

static void emit(const char* key, const wchar_t* value) {
    char utf8[65536];
    if (value == nullptr) {
        printf("%s=\n", key);
        return;
    }
    int written = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, utf8,
                                      static_cast<int>(sizeof(utf8)), nullptr, nullptr);
    if (written <= 0) {
        printf("%s=\n", key);
        return;
    }
    printf("%s=%s\n", key, utf8);
}

static int fail(const char* message) {
    fprintf(stderr, "%s\n", message);
    return 2;
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 2 || g_dzmcp_test_api.validate == nullptr) return fail("harness api");
    if (lstrcmpW(argv[1], L"validate") == 0) {
        int ok = g_dzmcp_test_api.validate();
        printf("ok=%d\ninclude_open=%d\n", ok, g_dzmcp_test_api.include_open());
        return ok == 1 ? 0 : 1;
    }
    if (lstrcmpW(argv[1], L"namespace") == 0) {
        if (argc != 4) return fail("namespace args");
        wchar_t out[256]{};
        int handle_open = 0;
        int status = g_dzmcp_test_api.ns(argv[3], argv[2], out, 256, &handle_open);
        printf("status=%d\nhandle_open=%d\n", status, handle_open);
        emit("namespace", out);
        g_dzmcp_test_api.close_ns();
        return status == 0 ? 1 : 0;
    }
    if (lstrcmpW(argv[1], L"pinned-build") != 0 && lstrcmpW(argv[1], L"unpinned-build") != 0 &&
        lstrcmpW(argv[1], L"build") != 0) {
        return fail("mode");
    }
    if (argc != 9) return fail("build args");
    if (lstrcmpW(argv[1], L"build") != 0) {
        int ok = g_dzmcp_test_api.validate();
        if (lstrcmpW(argv[1], L"unpinned-build") == 0) g_dzmcp_test_api.close_include();
        if (ok != 1 && lstrcmpW(argv[1], L"pinned-build") == 0) {
            printf("ok=0\ninclude_open=%d\nprefix_open=0\n", g_dzmcp_test_api.include_open());
            emit("command", L"");
            emit("pbo", L"");
            return 1;
        }
    }
    wchar_t command[32768]{};
    wchar_t pbo[2048]{};
    int include_open = 0;
    int prefix_open = 0;
    int clear = _wtoi(argv[2]);
    int pack_only = _wtoi(argv[3]);
    int built = g_dzmcp_test_api.command(
        clear, pack_only, argv[4], argv[5], argv[6], argv[7], argv[8],
        command, 32768, pbo, 2048, &include_open, &prefix_open);
    printf("ok=%d\ninclude_open=%d\nprefix_open=%d\n", built, include_open, prefix_open);
    emit("command", command);
    emit("pbo", pbo);
    return built == 1 ? 0 : 1;
}
