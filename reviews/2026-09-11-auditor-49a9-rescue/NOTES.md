# Rescue: extra_mods name-form host-independent test (49a9 follow-up)

Auditor proposal. After PR #3 merged the extra_mods name-form docs/error wording, orphan commit `211d71c2125da1eb8dd98f9cc852f82c8b2874cc` added `tools/tests/test_extra_mods_name_form.py` (WinDLL stub when missing; locks `bad_mod` / `bridge_mod_missing` wording and `server.py` prose) on the now-deleted branch `auditor/fb-20260909-213257-49a9-extra-mods-docs`. This restores only that test onto current `main`. No runtime or validation change — the wording it locks is already on `main`.
