// player_move (inbox c1cb): walk the on-foot local player without OS input
// through the engine's own move command. 4_World, beside MCP_Weapon.c and its
// modded PlayerBase: the per-tick hooks are PlayerBase.CommandHandler on both
// sides and PlayerBase.ConsumeMove on the server. config.cpp compiles the
// whole folder.
//
// The design was measured in game (DayZDiag 1.29, one client on localhost,
// spike run 0db321a8, key 106): the owner sets OverrideMovementSpeed and
// OverrideMovementAngle ENABLED (human.c:234-237) and sends a
// ScriptInputUserData request; while the request's window is open the server
// applies the same two overrides ENABLED in every ConsumeMove after super and
// in its CommandHandler, and DISABLED at the end. Both sides walked 0.76 m per
// 0.5 s and stopped at the same position. The owner's overrides alone do not
// move the player in multiplayer (inbox 4485: corrected back), and script
// fields of PlayerBaseMove never reach the server (spike run e1dd38d2), so
// neither is used here.

// ScriptInputUserData type of the request and of its release. Vanilla
// INPUT_UDT_* are 1..16 (_constants.c:2-19); this sits beside
// MCP_INPUT_UDT_WEAPON_FIRE/RAISE (MCP_Weapon.c:6-7), far above them.
const int MCP_INPUT_UDT_PLAYER_MOVE = 78541066;

//! One player_move request: what the owner applies and what the server's copy
//! receives. move_id is the owner's id of the move. speed is the
//! OverrideMovementSpeed value, 1 walk, 2 jog, 3 sprint (human.c:24-25).
//! direction is angle (angle_deg, relative to the heading), heading
//! (heading_deg, compass: 0 = north = +Z, 90 = east = +X) or to (target,
//! walked to on the horizontal plane until within radius). seconds is hold_s
//! for phase hold and ttl_s for phase press.
class MCPPlayerMoveRequest
{
	int move_id;
	string phase;
	float speed;
	string direction;
	float angle_deg;
	float heading_deg;
	vector target;
	float radius;
	float seconds;
};

//! Owner side of player_move: one move at a time. Begin applies
//! OverrideMovementSpeed and OverrideMovementAngle ENABLED on the local
//! player's HumanInputController (ENABLED holds until DISABLED, human.c:7-12);
//! OnCommandHandler applies them again on every command tick of the local
//! player, the angle recomputed from the current heading and position;
//! ReleaseAll applies DISABLED, only when this code enabled them, and sends the
//! server its release. Nothing stays enabled without a scheduled end: hold_s or
//! ttl_s (30 s at most), the arrival, phase=release, RestoreGameplay, a change,
//! death, unconsciousness, restraint or seat of the local player, and shutdown.
//! Maintained from MCPClientBridge.OnTick on the
//! MCPWeaponControl.MaintainFromTick pattern. The camera is never turned and
//! AlignDirectionWS is not used (human.c:1376-1377: it handles no replay).
//!
//! Angles. GetHeadingAngle is radians, -PI..PI (human.c:27-28). Vanilla turns a
//! heading h into the facing x = cos(h + PI/2), z = sin(h + PI/2)
//! (miscgameplayfunctions.c:726-733), so h = 0 faces +Z and h grows
//! counter-clockwise: the compass heading is -h (crosshairselector.c:254).
//! OverrideMovementAngle names no unit. This code passes degrees, -180..180
//! relative to the heading, positive to the right: the convention of
//! HumanCommandMove's movement and input angles (human.c:439-445) and of
//! StartMeleeEvadeA (-90 left, 90 right,
//! dayzplayermeleefightlogic_lightheavy.c:216-229). Not measured in game yet.
class MCPPlayerMoveControl
{
	// Mirrored by player_move.py; tools/tests keep the copies equal. Values
	// outside the bounds are refused, never clamped.
	static const float HOLD_MAX_S = 30.0;
	static const float PRESS_MAX_TTL_S = 30.0;
	static const float ANGLE_ABS_MAX_DEG = 180.0;
	static const float HEADING_MAX_DEG = 360.0;
	static const float ARRIVE_RADIUS_MIN_M = 0.2;
	static const float ARRIVE_RADIUS_MAX_M = 5.0;
	static const float TO_MAX_DISTANCE_M = 200.0;
	// The server's deadman: the move's seconds plus this, counted from the
	// request's arrival there. The owner's release normally ends it first.
	static const float SERVER_DEADMAN_MARGIN_S = 0.5;

	// The client bridge OnTick count of the latest MaintainFromTick: the
	// counter a result's tick_dispatch and player_trace's tick report.
	static int s_Tick;
	static bool s_Active;
	// True once this code applied ENABLED to s_Player's controller.
	static bool s_Armed;
	static PlayerBase s_Player;
	// The id of the active or last move; a hold's job waits on it.
	static int s_Gen;
	// The active move, or the last one once released (until the next Begin).
	static ref MCPPlayerMoveRequest s_Request;
	static float s_StartS;
	static float s_DueS;
	static string s_ServerRequest;
	static vector s_StartPos;
	static int s_StartTick;
	static float s_AppliedAngleDeg;
	static int s_CommandTicks;
	// The last release, read by the job that waits on it and by not_held.
	static int s_ReleasedGen;
	static string s_ReleasedBy;
	static int s_ReleaseTick;
	static float s_ReleaseTimeS;
	static bool s_ReleasePosKnown;
	static vector s_ReleasePos;
	static bool s_ReleaseArrived;
	// A sent move released here whose release has not left yet, and its id.
	static bool s_ServerReleasePending;
	static int s_ServerReleaseId;

	static bool IsActive()
	{
		return s_Active;
	}

	static int Generation()
	{
		return s_Gen;
	}

	// The id the next move takes. Begin adopts it; a start that is never sent
	// leaves it free. Ids only grow in this process (s_Gen is static), and the
	// server copy refuses a start whose id it has already seen (stale_move_id).
	static int NextMoveId()
	{
		return s_Gen + 1;
	}

	static bool WasReleased(int generation)
	{
		return s_ReleasedGen == generation;
	}

	static bool HasReleased()
	{
		return s_ReleasedGen > 0;
	}

	static MCPPlayerMoveRequest Request()
	{
		return s_Request;
	}

	static float StartS()
	{
		return s_StartS;
	}

	static float DueS()
	{
		return s_DueS;
	}

	static string ServerRequest()
	{
		return s_ServerRequest;
	}

	static vector StartPos()
	{
		return s_StartPos;
	}

	static int StartTick()
	{
		return s_StartTick;
	}

	static float AppliedAngleDeg()
	{
		return s_AppliedAngleDeg;
	}

	static int CommandTicks()
	{
		return s_CommandTicks;
	}

	static string ReleasedBy()
	{
		return s_ReleasedBy;
	}

	static int ReleaseTick()
	{
		return s_ReleaseTick;
	}

	static float ReleaseTimeS()
	{
		return s_ReleaseTimeS;
	}

	static bool ReleasePosKnown()
	{
		return s_ReleasePosKnown;
	}

	static vector ReleasePos()
	{
		return s_ReleasePos;
	}

	static bool ReleaseArrived()
	{
		return s_ReleaseArrived;
	}

	static string SpeedName(float speed)
	{
		if (speed == 1.0)
		{
			return "walk";
		}
		if (speed == 2.0)
		{
			return "jog";
		}
		if (speed == 3.0)
		{
			return "sprint";
		}
		return "";
	}

	// NaN fails every comparison, so it is the one value that needs the
	// self-test; float.MAX stands for an infinity, as IsStrictFinite does.
	static bool IsFiniteValue(float value)
	{
		if (value != value)
		{
			return false;
		}
		if (value >= float.MAX)
		{
			return false;
		}
		if (value <= -float.MAX)
		{
			return false;
		}
		return true;
	}

	static bool IsFiniteVector(vector value)
	{
		if (!IsFiniteValue(value[0]))
		{
			return false;
		}
		if (!IsFiniteValue(value[1]))
		{
			return false;
		}
		return IsFiniteValue(value[2]);
	}

	static float HorizontalDistance(vector from, vector target)
	{
		float dx;
		float dz;
		dx = target[0] - from[0];
		dz = target[2] - from[2];
		return Math.Sqrt(dx * dx + dz * dz);
	}

	// Compass degrees (0 = +Z, 90 = +X) of a GetHeadingAngle value, -180..180.
	static float CompassFromHeading(float headingRad)
	{
		return -headingRad * Math.RAD2DEG;
	}

	// Compass degrees of the horizontal direction from one point to another.
	// Math.Atan2(y, x) is radians (enmath.c:388-399); with y = dx and x = dz it
	// is 0 toward +Z and 90 toward +X.
	static float BearingDeg(vector from, vector target)
	{
		float dx;
		float dz;
		dx = target[0] - from[0];
		dz = target[2] - from[2];
		return Math.Atan2(dx, dz) * Math.RAD2DEG;
	}

	// -180..180. The callers pass a wanted direction (0..360 or -180..180)
	// minus a compass heading (-180..180): one turn and a half at most.
	static float WrapDeg180(float angle)
	{
		float wrapped;
		wrapped = angle;
		if (wrapped > 180.0)
		{
			wrapped = wrapped - 360.0;
		}
		if (wrapped > 180.0)
		{
			wrapped = wrapped - 360.0;
		}
		if (wrapped < -180.0)
		{
			wrapped = wrapped + 360.0;
		}
		if (wrapped < -180.0)
		{
			wrapped = wrapped + 360.0;
		}
		return wrapped;
	}

	// The angle for OverrideMovementAngle: the request's own for direction
	// angle, else the wanted compass direction minus the current compass
	// heading. Each side calls it with its own player, so the server steers
	// from its own heading and position.
	static float RelativeAngleDeg(PlayerBase player, MCPPlayerMoveRequest request)
	{
		HumanInputController hic;
		float compass;
		float wanted;
		if (request.direction == "angle")
		{
			return request.angle_deg;
		}
		hic = player.GetInputController();
		if (!hic)
		{
			return 0.0;
		}
		compass = CompassFromHeading(hic.GetHeadingAngle());
		wanted = request.heading_deg;
		if (request.direction == "to")
		{
			wanted = BearingDeg(player.PhysicsGetPositionWS(), request.target);
		}
		return WrapDeg180(wanted - compass);
	}

	// Direction to only: within radius of the target on the horizontal plane.
	static bool HasArrived(PlayerBase player, MCPPlayerMoveRequest request)
	{
		if (request.direction != "to")
		{
			return false;
		}
		return HorizontalDistance(player.PhysicsGetPositionWS(), request.target) <= request.radius;
	}

	static void Enable(HumanInputController hic, float speed, float angleDeg)
	{
		hic.OverrideMovementSpeed(HumanInputControllerOverrideType.ENABLED, speed);
		hic.OverrideMovementAngle(HumanInputControllerOverrideType.ENABLED, angleDeg);
	}

	static void Disable(HumanInputController hic)
	{
		hic.OverrideMovementSpeed(HumanInputControllerOverrideType.DISABLED, 0.0);
		hic.OverrideMovementAngle(HumanInputControllerOverrideType.DISABLED, 0.0);
	}

	// The values, the same rule on the owner (DispatchPlayerMove) and on the
	// server's copy of a request. "" when they pass.
	static string RequestRefusal(MCPPlayerMoveRequest request)
	{
		if (!request)
		{
			return "bad_args";
		}
		if (request.phase != "hold" && request.phase != "press")
		{
			return "bad_args";
		}
		if (request.speed != 1.0 && request.speed != 2.0 && request.speed != 3.0)
		{
			return "bad_speed";
		}
		if (request.direction == "angle")
		{
			if (!IsFiniteValue(request.angle_deg))
			{
				return "bad_angle_deg";
			}
			if (request.angle_deg < -ANGLE_ABS_MAX_DEG)
			{
				return "bad_angle_deg";
			}
			if (request.angle_deg > ANGLE_ABS_MAX_DEG)
			{
				return "bad_angle_deg";
			}
		}
		else if (request.direction == "heading")
		{
			if (!IsFiniteValue(request.heading_deg))
			{
				return "bad_heading_deg";
			}
			if (request.heading_deg < 0.0)
			{
				return "bad_heading_deg";
			}
			if (request.heading_deg >= HEADING_MAX_DEG)
			{
				return "bad_heading_deg";
			}
		}
		else if (request.direction == "to")
		{
			if (!IsFiniteVector(request.target))
			{
				return "bad_to";
			}
			if (!IsFiniteValue(request.radius))
			{
				return "bad_arrive_radius_m";
			}
			if (request.radius < ARRIVE_RADIUS_MIN_M)
			{
				return "bad_arrive_radius_m";
			}
			if (request.radius > ARRIVE_RADIUS_MAX_M)
			{
				return "bad_arrive_radius_m";
			}
		}
		else
		{
			return "bad_args";
		}
		return SecondsRefusal(request.phase, request.seconds);
	}

	// hold_s for hold, ttl_s for press: finite and in (0, max]. An absent key
	// arrives as 0 and is refused here (fb-20260930-065425-8779).
	static string SecondsRefusal(string phase, float seconds)
	{
		string code;
		float maximum;
		code = "bad_ttl_s";
		maximum = PRESS_MAX_TTL_S;
		if (phase == "hold")
		{
			code = "bad_hold_s";
			maximum = HOLD_MAX_S;
		}
		if (!IsFiniteValue(seconds))
		{
			return code;
		}
		if (seconds <= 0.0)
		{
			return code;
		}
		if (seconds > maximum)
		{
			return code;
		}
		return "";
	}

	// Who may keep moving: the predicates the weapon verbs use, IsAlive
	// (object.c:523), IsUnconscious (playerbase.c:3655), IsRestrained
	// (playerbase.c:2040) and IsInVehicle (dayzplayerimplement.c:465-468: the
	// vehicle command or a Transport parent), which is seated here, and an
	// input controller (human.c:1424). The owner and the server's copy both call
	// it, at the start and on every tick of the move.
	static string ActorRefusal(PlayerBase player)
	{
		if (!player)
		{
			return "no_player";
		}
		if (!player.IsAlive())
		{
			return "player_dead";
		}
		if (player.IsUnconscious())
		{
			return "player_unconscious";
		}
		if (player.IsRestrained())
		{
			return "player_restrained";
		}
		if (player.IsInVehicle())
		{
			return "seated";
		}
		if (!player.GetInputController())
		{
			return "no_input_controller";
		}
		return "";
	}

	// Who may start: ActorRefusal, then the move command (GetCurrentCommandID,
	// human.c:1439; COMMANDID_MOVE, dayzplayer.c:696). Swimming, falling, a
	// ladder or any other command refuses. Only at the start: a fall or a climb
	// during the move does not end it; its hold_s or ttl_s does.
	static string StartRefusal(PlayerBase player)
	{
		string refusal;
		refusal = ActorRefusal(player);
		if (refusal != "")
		{
			return refusal;
		}
		if (player.GetCurrentCommandID() != DayZPlayerConstants.COMMANDID_MOVE)
		{
			return "not_in_move_command";
		}
		return "";
	}

	// Direction to only: the target at most TO_MAX_DISTANCE_M away.
	static string DistanceRefusal(PlayerBase player, MCPPlayerMoveRequest request)
	{
		if (request.direction != "to")
		{
			return "";
		}
		if (HorizontalDistance(player.PhysicsGetPositionWS(), request.target) > TO_MAX_DISTANCE_M)
		{
			return "bad_to";
		}
		return "";
	}

	// Starts the move and applies the overrides once at dispatch;
	// OnCommandHandler keeps them. The caller checked the request, the actor,
	// that no move is active, and sent the server its copy first. nowS is the
	// dispatch's GetTickTime. Returns the move id.
	static int Begin(PlayerBase player, MCPPlayerMoveRequest request, float nowS, string serverRequest)
	{
		s_Gen = request.move_id;
		s_Active = true;
		s_Armed = false;
		s_Player = player;
		s_Request = request;
		s_StartS = nowS;
		s_DueS = nowS + request.seconds;
		s_ServerRequest = serverRequest;
		s_StartPos = player.PhysicsGetPositionWS();
		s_StartTick = s_Tick;
		s_AppliedAngleDeg = 0.0;
		s_CommandTicks = 0;
		Apply(player);
		return s_Gen;
	}

	static void Apply(PlayerBase player)
	{
		HumanInputController hic;
		float angle;
		hic = player.GetInputController();
		if (!hic)
		{
			return;
		}
		angle = RelativeAngleDeg(player, s_Request);
		Enable(hic, s_Request.speed, angle);
		s_Armed = true;
		s_AppliedAngleDeg = angle;
	}

	// Records why the move ended, then DISABLED on the controller this code
	// enabled. The active state is cleared first, so a path that reaches here
	// again finds nothing to release. A sent move sends the server its release
	// (FlushServerRelease), whatever the cause; at shutdown ShutdownRelease then
	// drops what could not be sent.
	static void ReleaseAll(string why)
	{
		HumanInputController hic;
		if (!s_Active)
		{
			return;
		}
		s_Active = false;
		s_ReleasedGen = s_Gen;
		s_ReleasedBy = why;
		s_ReleaseTick = s_Tick;
		s_ReleaseTimeS = -1.0;
		if (GetGame())
		{
			s_ReleaseTimeS = GetGame().GetTickTime();
		}
		s_ReleaseArrived = false;
		if (why == "arrived")
		{
			s_ReleaseArrived = true;
		}
		s_ReleasePosKnown = false;
		if (s_Player)
		{
			s_ReleasePos = s_Player.PhysicsGetPositionWS();
			s_ReleasePosKnown = true;
			if (s_Armed)
			{
				hic = s_Player.GetInputController();
				if (hic)
				{
					Disable(hic);
				}
			}
		}
		s_Armed = false;
		s_Player = null;
		if (s_ServerRequest == "sent")
		{
			s_ServerReleasePending = true;
			s_ServerReleaseId = s_Gen;
			FlushServerRelease();
		}
	}

	// From MCPClientBridge.Shutdown: an active move ends as shutdown, then the
	// server release that ending or an earlier one left waiting gets one last
	// best-effort send (FlushServerRelease sends only while the local player
	// exists and the input channel takes it) before it is dropped, since no
	// OnTick will retry it. The server copy otherwise ends on its own checks (no
	// identity left, not alive) or, last, on its deadman.
	static void ShutdownRelease()
	{
		ReleaseAll("shutdown");
		FlushServerRelease();
		s_ServerReleasePending = false;
	}

	// The scheduled end: hold_s for hold, ttl_s for press.
	static void ReleaseDue()
	{
		if (s_Request && s_Request.phase == "hold")
		{
			ReleaseAll("hold");
			return;
		}
		ReleaseAll("ttl");
	}

	// From the local player's CommandHandler on every command tick while a move
	// is active: the end checks first, then the overrides again with the angle
	// recomputed from the current heading and position.
	static void OnCommandHandler(PlayerBase player)
	{
		if (!s_Active)
		{
			return;
		}
		if (player != s_Player)
		{
			return;
		}
		if (!GetGame())
		{
			return;
		}
		if (ActorRefusal(player) != "")
		{
			ReleaseAll("player_changed");
			return;
		}
		if (GetGame().GetTickTime() >= s_DueS)
		{
			ReleaseDue();
			return;
		}
		if (HasArrived(player, s_Request))
		{
			ReleaseAll("arrived");
			return;
		}
		Apply(player);
		s_CommandTicks = s_CommandTicks + 1;
	}

	// Once per bridge OnTick, before the job runner, so a hold answers in the
	// tick of its release. Returns before any engine call while no move is
	// active and no release waits. A missing, changed, dead, unconscious,
	// restrained or seated local player releases at once.
	static void MaintainFromTick(int bridgeTick)
	{
		PlayerBase live;
		s_Tick = bridgeTick;
		FlushServerRelease();
		if (!s_Active)
		{
			return;
		}
		live = null;
		if (GetGame())
		{
			live = PlayerBase.Cast(GetGame().GetPlayer());
		}
		if (!live)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (live != s_Player)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (ActorRefusal(live) != "")
		{
			ReleaseAll("player_changed");
			return;
		}
		if (GetGame().GetTickTime() >= s_DueS)
		{
			ReleaseDue();
			return;
		}
		if (HasArrived(live, s_Request))
		{
			ReleaseAll("arrived");
		}
	}

	// sent, unavailable (not a multiplayer client: offline this one process is
	// the server and the local overrides are the whole move, as
	// SendLiftWeaponSync assumes, playerbase.c:8446-8458) or input_busy (the
	// input channel is taken or full, gameplay.c:130-131). The caller sends
	// before the owner's overrides change, so input_busy moves neither side.
	// The release of the previous move goes out first: the server keeps a move
	// running when a later start is refused, so no start may stand in for a
	// release. While that release cannot be sent the start is input_busy too.
	static string SendStart(MCPPlayerMoveRequest request)
	{
		ScriptInputUserData message;
		if (!GetGame())
		{
			return "unavailable";
		}
		if (!GetGame().IsMultiplayer())
		{
			return "unavailable";
		}
		if (!GetGame().IsClient())
		{
			return "unavailable";
		}
		FlushServerRelease();
		if (s_ServerReleasePending)
		{
			return "input_busy";
		}
		if (!ScriptInputUserData.CanStoreInputUserData())
		{
			return "input_busy";
		}
		message = new ScriptInputUserData();
		message.Write(MCP_INPUT_UDT_PLAYER_MOVE);
		message.Write(true);
		message.Write(request.move_id);
		message.Write(request.phase);
		message.Write(request.speed);
		message.Write(request.direction);
		message.Write(request.angle_deg);
		message.Write(request.heading_deg);
		message.Write(request.target);
		message.Write(request.radius);
		message.Write(request.seconds);
		message.Send();
		return "sent";
	}

	// The release of a sent move goes out as soon as there is a local player
	// and the input channel takes it; until then every OnTick retries. It names
	// its move, so it never ends a newer one. The server's own deadman ends its
	// copy in any case.
	static void FlushServerRelease()
	{
		ScriptInputUserData message;
		if (!s_ServerReleasePending)
		{
			return;
		}
		if (!GetGame())
		{
			s_ServerReleasePending = false;
			return;
		}
		if (!GetGame().IsMultiplayer() || !GetGame().IsClient())
		{
			s_ServerReleasePending = false;
			return;
		}
		if (!GetGame().GetPlayer())
		{
			return;
		}
		if (!ScriptInputUserData.CanStoreInputUserData())
		{
			return;
		}
		message = new ScriptInputUserData();
		message.Write(MCP_INPUT_UDT_PLAYER_MOVE);
		message.Write(false);
		message.Write(s_ServerReleaseId);
		message.Send();
		s_ServerReleasePending = false;
	}

	// A start's values after its type, flag and id, in SendStart's order. false
	// when any read fails; the server then refuses it.
	static bool ReadRequest(ParamsReadContext ctx, MCPPlayerMoveRequest request)
	{
		string phase;
		float speed;
		string direction;
		float angleDeg;
		float headingDeg;
		vector target;
		float radius;
		float seconds;
		phase = "";
		speed = 0.0;
		direction = "";
		angleDeg = 0.0;
		headingDeg = 0.0;
		target = vector.Zero;
		radius = 0.0;
		seconds = 0.0;
		if (!ctx.Read(phase))
		{
			return false;
		}
		if (!ctx.Read(speed))
		{
			return false;
		}
		if (!ctx.Read(direction))
		{
			return false;
		}
		if (!ctx.Read(angleDeg))
		{
			return false;
		}
		if (!ctx.Read(headingDeg))
		{
			return false;
		}
		if (!ctx.Read(target))
		{
			return false;
		}
		if (!ctx.Read(radius))
		{
			return false;
		}
		if (!ctx.Read(seconds))
		{
			return false;
		}
		request.phase = phase;
		request.speed = speed;
		request.direction = direction;
		request.angle_deg = angleDeg;
		request.heading_deg = headingDeg;
		request.target = target;
		request.radius = radius;
		request.seconds = seconds;
		return true;
	}
};

modded class PlayerBase
{
	// Server copy of the owner's player_move, per player: the accepted request,
	// its deadman, and whether this copy enabled the overrides. Only the server
	// instance accepts a request, so elsewhere these stay unset and the hooks
	// below cost one test per tick. No ref on entities.
	protected ref MCPPlayerMoveRequest m_MCPMoveActive;
	protected float m_MCPMoveDeadlineS;
	protected bool m_MCPMoveApplied;
	// Applications from ConsumeMove and from CommandHandler, logged at the end.
	protected int m_MCPMoveConsumed;
	protected int m_MCPMoveHandled;
	// The highest move id this copy has seen in a start or a release. A start
	// must carry a greater one, so a duplicate or replayed start cannot re-arm a
	// move the owner ended. A new PlayerBase (a respawn, a body loaded on
	// connect) starts from 0, and the owner's id only grows within its process
	// (MCPPlayerMoveControl.s_Gen is static), so that is consistent; a reconnect
	// into this same body resets it (OnReconnect), as that client may be a new
	// process counting from 1 again.
	protected int m_MCPMoveLastId;

	// playerbase.c:6250. Ours stops here, as the weapon requests do
	// (MCP_Weapon.c OnInputUserDataProcess).
	override bool OnInputUserDataProcess(int userDataType, ParamsReadContext ctx)
	{
		if (userDataType == MCP_INPUT_UDT_PLAYER_MOVE)
		{
			MCPMoveReadRequest(ctx);
			return true;
		}
		return super.OnInputUserDataProcess(userDataType, ctx);
	}

	// A start (true, its id, its values) or a release (false, its id), in the
	// order SendStart and FlushServerRelease write them.
	protected void MCPMoveReadRequest(ParamsReadContext ctx)
	{
		bool isStart;
		int moveId;
		MCPPlayerMoveRequest request;
		isStart = false;
		moveId = 0;
		if (!ctx.Read(isStart))
		{
			MCPMoveVerdict(false, 0, "read_failed");
			return;
		}
		if (!ctx.Read(moveId))
		{
			MCPMoveVerdict(false, 0, "read_failed");
			return;
		}
		if (!isStart)
		{
			MCPMoveReadRelease(moveId);
			return;
		}
		request = new MCPPlayerMoveRequest();
		request.move_id = moveId;
		if (!MCPPlayerMoveControl.ReadRequest(ctx, request))
		{
			MCPMoveVerdict(false, moveId, "read_failed");
			return;
		}
		MCPMoveAccept(request);
	}

	// The owner's release ends the move it names, never a newer one. Its id
	// counts as seen, so a start of that move arriving after it is refused.
	protected void MCPMoveReadRelease(int moveId)
	{
		if (moveId > m_MCPMoveLastId)
		{
			m_MCPMoveLastId = moveId;
		}
		if (!m_MCPMoveActive)
		{
			MCPMoveNote(moveId, "release_without_move");
			return;
		}
		if (m_MCPMoveActive.move_id != moveId)
		{
			MCPMoveNote(moveId, "release_of_another_move");
			return;
		}
		MCPMoveServerRelease("client");
	}

	// Every check runs before anything changes: the instance, the id's
	// freshness, the owner still being there, the values, the actor and the
	// move command, and the target distance from this copy's own position. A
	// refused start leaves whatever this copy runs untouched. Only an accepted
	// one replaces it: the owner releases a move before it starts the next
	// (MCPPlayerMoveControl.SendStart), so a move still running here lost that
	// release on the way.
	protected void MCPMoveAccept(MCPPlayerMoveRequest request)
	{
		string refusal;
		// The server instance of this player only (dayzplayer.c:1070-1072, :1171).
		if (GetInstanceType() != DayZPlayerInstanceType.INSTANCETYPE_SERVER)
		{
			MCPMoveVerdict(false, request.move_id, "not_server");
			return;
		}
		if (!GetGame())
		{
			MCPMoveVerdict(false, request.move_id, "no_game");
			return;
		}
		refusal = MCPMoveFreshnessRefusal(request.move_id);
		if (refusal == "")
		{
			// The id is used up now, accepted or not: the owner never sends the
			// same start twice, so a later copy of it can only be a replay.
			m_MCPMoveLastId = request.move_id;
			refusal = MCPMoveOwnerRefusal();
		}
		if (refusal == "")
		{
			refusal = MCPPlayerMoveControl.RequestRefusal(request);
		}
		if (refusal == "")
		{
			refusal = MCPPlayerMoveControl.StartRefusal(this);
		}
		if (refusal == "")
		{
			refusal = MCPPlayerMoveControl.DistanceRefusal(this, request);
		}
		if (refusal != "")
		{
			MCPMoveVerdict(false, request.move_id, refusal);
			return;
		}
		MCPMoveServerRelease("superseded");
		m_MCPMoveActive = request;
		m_MCPMoveDeadlineS = GetGame().GetTickTime() + request.seconds + MCPPlayerMoveControl.SERVER_DEADMAN_MARGIN_S;
		m_MCPMoveApplied = false;
		m_MCPMoveConsumed = 0;
		m_MCPMoveHandled = 0;
		MCPMoveVerdict(true, request.move_id, "");
	}

	// "" for an id greater than any this copy has seen, else why not: ids start
	// at 1, and an id already seen is a duplicate or a replay.
	protected string MCPMoveFreshnessRefusal(int moveId)
	{
		if (moveId <= 0)
		{
			return "bad_move_id";
		}
		if (moveId <= m_MCPMoveLastId)
		{
			return "stale_move_id";
		}
		return "";
	}

	// The server copy moves only while its owner is still there: a body whose
	// client is gone (no identity left, Man.GetIdentity at man.c:21, or vanilla's
	// disconnect already processed, playerbase.c:2453-2461) stops at once instead
	// of walking on a request nobody will release.
	protected string MCPMoveOwnerRefusal()
	{
		if (!GetIdentity())
		{
			return "no_identity";
		}
		if (IsPlayerDisconnected())
		{
			return "disconnected";
		}
		return "";
	}

	// Every tick of the server's move, from ConsumeMove after super and from
	// CommandHandler: the end checks first (the owner still there, the actor,
	// its own deadman, its own arrival), then the two overrides again with the
	// angle recomputed from this copy's own heading and position. The deadman,
	// the move's seconds plus SERVER_DEADMAN_MARGIN_S from the start's arrival,
	// is the last bound when nothing else ends it.
	protected void MCPMoveServerTick(bool fromConsume)
	{
		string refusal;
		HumanInputController hic;
		float angle;
		if (!m_MCPMoveActive)
		{
			return;
		}
		if (!GetGame())
		{
			MCPMoveServerRelease("no_game");
			return;
		}
		refusal = MCPMoveOwnerRefusal();
		if (refusal == "")
		{
			refusal = MCPPlayerMoveControl.ActorRefusal(this);
		}
		if (refusal != "")
		{
			MCPMoveServerRelease(refusal);
			return;
		}
		if (GetGame().GetTickTime() > m_MCPMoveDeadlineS)
		{
			MCPMoveServerRelease("expired");
			return;
		}
		if (MCPPlayerMoveControl.HasArrived(this, m_MCPMoveActive))
		{
			MCPMoveServerRelease("arrived");
			return;
		}
		hic = GetInputController();
		angle = MCPPlayerMoveControl.RelativeAngleDeg(this, m_MCPMoveActive);
		MCPPlayerMoveControl.Enable(hic, m_MCPMoveActive.speed, angle);
		m_MCPMoveApplied = true;
		if (fromConsume)
		{
			m_MCPMoveConsumed = m_MCPMoveConsumed + 1;
		}
		else
		{
			m_MCPMoveHandled = m_MCPMoveHandled + 1;
		}
	}

	// DISABLED only when this copy applied ENABLED (human.c:7-12), then one
	// script-log line with the applications counted.
	protected void MCPMoveServerRelease(string why)
	{
		HumanInputController hic;
		int moveId;
		string line;
		if (!m_MCPMoveActive)
		{
			return;
		}
		moveId = m_MCPMoveActive.move_id;
		m_MCPMoveActive = null;
		if (m_MCPMoveApplied)
		{
			hic = GetInputController();
			if (hic)
			{
				MCPPlayerMoveControl.Disable(hic);
			}
		}
		m_MCPMoveApplied = false;
		line = "[DayZ_MCP] player_move server released move=" + moveId.ToString() + " reason=" + why;
		line = line + " consumed=" + m_MCPMoveConsumed.ToString() + " handled=" + m_MCPMoveHandled.ToString();
		Print(line);
	}

	// One script-log line per start: the server's own verdict.
	protected void MCPMoveVerdict(bool accepted, int moveId, string reason)
	{
		string line;
		line = "[DayZ_MCP] player_move server accepted=0";
		if (accepted)
		{
			line = "[DayZ_MCP] player_move server accepted=1";
		}
		line = line + " move=" + moveId.ToString() + " reason=" + reason;
		Print(line);
	}

	protected void MCPMoveNote(int moveId, string reason)
	{
		Print("[DayZ_MCP] player_move server ignored move=" + moveId.ToString() + " reason=" + reason);
	}

	// A client reconnecting into this body (missionserver.c:620-626) may be a
	// new process whose move ids start at 1 again: nothing of the old
	// connection's move runs on, and its ids are fresh for this body again.
	override void OnReconnect()
	{
		super.OnReconnect();
		MCPMoveServerRelease("reconnect");
		m_MCPMoveLastId = 0;
	}

	// super first. The server's copy runs its move before the local-player
	// test, which it never passes (GetPlayer() is null there). The rest is the
	// local player only, while a move is active.
	override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)
	{
		PlayerBase live;
		super.CommandHandler(pDt, pCurrentCommandID, pCurrentCommandFinished);
		if (m_MCPMoveActive)
		{
			MCPMoveServerTick(false);
		}
		if (!MCPPlayerMoveControl.IsActive())
		{
			return;
		}
		if (!GetGame())
		{
			return;
		}
		// GetPlayer() returns DayZPlayer (game.c:946). Identity check needs PlayerBase.Cast.
		live = PlayerBase.Cast(GetGame().GetPlayer());
		if (this != live)
		{
			return;
		}
		MCPPlayerMoveControl.OnCommandHandler(this);
	}

#ifdef FEATURE_NETWORK_RECONCILIATION
	// Authority (pawn.c:270-276; defines.c:64 sets the feature). Vanilla
	// PlayerBase has no override. After super, so the consumed move's own
	// inputs come first: the measured design applied the overrides here on
	// every consumed move (spike key 106: calls=90 applied=90).
	protected override event void ConsumeMove(PawnMove pMove)
	{
		super.ConsumeMove(pMove);
		if (m_MCPMoveActive)
		{
			MCPMoveServerTick(true);
		}
	}
#endif
};
