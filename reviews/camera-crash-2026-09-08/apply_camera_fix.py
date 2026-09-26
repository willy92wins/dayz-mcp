"""One-pass, byte-verified edit; refuses to overwrite another session's changes."""
from pathlib import Path
import hashlib


OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PATH = ROOT / "addon/scripts/5_Mission/MCPClientBridge.c"
before = (OUT / "MCPClientBridge.c.BEFORE").read_bytes()
assert PATH.read_bytes() == before, "bridge changed since this lane's snapshot; stop"
newline = b"\r\n" if b"\r\n" in before else b"\n"


def encoded(text):
    return text.encode("utf-8").replace(b"\n", newline)


start = before.index(b"\tprotected MCPCamera BuildCameraResult(string mode)")
end = before.index(b"\tprotected bool ArrayToVector(", start)
replacement = encoded('''\t// GetCurrentCamera crashes inside the native getter before it can return
\t// null (SUB_BRZ RPTs 2026-09-08, deployed BuildCameraResult:3664).
\t// A local player does not prove that the native scripted camera exists.
\t// Inspect only our retained instance; absence is not proof of player view.
\tprotected string CameraReadError()
\t{
\t\tif (!IsClientInGame())
\t\t{
\t\t\treturn "client_not_in_game";
\t\t}

\t\tPlayerBase cameraPlayer = PlayerBase.Cast(GetGame().GetPlayer());
\t\tif (!cameraPlayer)
\t\t{
\t\t\treturn "camera_unavailable_player";
\t\t}

\t\t// Vehicle view can override a scripted camera. Check parentage too:
\t\t// a missing client vehicle command is not evidence of being on foot.
\t\tif (cameraPlayer.GetCommand_Vehicle())
\t\t{
\t\t\treturn "camera_unavailable_vehicle";
\t\t}
\t\tif (cameraPlayer.GetParent())
\t\t{
\t\t\treturn "camera_unavailable_parented_player";
\t\t}

\t\tif (!m_ActiveCam)
\t\t{
\t\t\treturn "camera_unavailable_no_scripted_camera";
\t\t}
\t\tif (!m_ActiveCam.IsActive())
\t\t{
\t\t\treturn "camera_unavailable_inactive";
\t\t}

\t\treturn "";
\t}

\tprotected MCPCamera BuildCameraResult(string mode)
\t{
\t\tMCPCamera camera = new MCPCamera();
\t\tcamera.applied_mode = mode;

\t\t// Shared by camera_get and the camera_set report (outside Dispatch).
\t\t// Rejected snapshots keep pos/matrix/dir empty (ctor-initialized).
\t\tstring cameraError = CameraReadError();
\t\tif (cameraError != "")
\t\t{
\t\t\tcamera.ok = false;
\t\t\tcamera.viewport_moved = false;
\t\t\tcamera.error = cameraError;
\t\t\treturn camera;
\t\t}

\t\tcamera.ok = true;
\t\tCamera current = m_ActiveCam;
\t\tvector matrix[4];
\t\tcurrent.GetTransform(matrix);
\t\tMatrixToArray(matrix, camera.matrix);
\t\tVectorToArray(current.GetWorldPosition(), camera.pos);
\t\tVectorToArray(matrix[2], camera.dir);
\t\tcamera.fov = Camera.GetCurrentFOV();
\t\tcamera.interpolation_complete = Camera.IsInterpolationComplete();
\t\tcamera.viewport_moved = true;
\t\treturn camera;
\t}

''')
after = before[:start] + replacement + before[end:]
settle_before = encoded('''\t\tif (job.phase == CAMERA_PHASE_SETTLE)
\t\t{
\t\t\tbool interpolationComplete = Camera.IsInterpolationComplete();
''')
settle_after = encoded('''\t\tif (job.phase == CAMERA_PHASE_SETTLE)
\t\t{
\t\t\t// Do not query global camera state after losing the scripted view.
\t\t\t// REPORT uses BuildCameraResult to return the named unavailable state.
\t\t\tif (CameraReadError() != "")
\t\t\t{
\t\t\t\tjob.phase = CAMERA_PHASE_REPORT;
\t\t\t\treturn true;
\t\t\t}
\t\t\tbool interpolationComplete = Camera.IsInterpolationComplete();
''')
assert after.count(settle_before) == 1
after = after.replace(settle_before, settle_after, 1)
assert PATH.read_bytes() == before, "bridge changed during preparation; stop"
PATH.write_bytes(after)
assert PATH.read_bytes() == after and PATH.stat().st_size == len(after)
print(f"VERIFIED {PATH}: {len(before)} -> {len(after)} bytes")
print(f"SHA256 before={hashlib.sha256(before).hexdigest()}")
print(f"SHA256 after={hashlib.sha256(after).hexdigest()}")
