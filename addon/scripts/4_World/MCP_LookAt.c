// player_look_at. ONE_FRAME aim overrides measured 2026-10-08: v radians once
// per pulse, positive X turns right (compass increases), positive Y pitches up.
// Success is the measured camera direction, not the commanded pulse.
// The report type lives in World: Mission's MCPLookAt is a later module.
//
// 33c5, c616, cc75: in multiplayer the owner's aim pulses never reach the
// server, whose reconciliation then pulls the camera back to a heading that
// never turned (the round's client RPT: every look that ended in deadline had
// NwkMovement corrections inside its window). The server is sent a copy of
// the look, as player_move sends its move (MCP_PlayerMove.c): it pulses its
// own aim the same way until its heading is within BAND_DEG of the target's
// bearing, the owner's release or its deadman. Yaw only.

// ScriptInputUserData type of the look's server copy and of its release.
// Vanilla INPUT_UDT_* are 1..16 (_constants.c:2-19); the next id free after
// MCP_INPUT_UDT_PLAYER_MOVE (MCP_PlayerMove.c), 78541067 being
// MCP_RPC_HANDS_ATTACH (MCP_Weapon.c).
const int MCP_INPUT_UDT_PLAYER_LOOK = 78541068;

class MCPLookAtReport
{
	bool converged;
	float error_initial_deg;
	float error_final_deg;
	int ticks;
	float duration_s;
	string pose;
	string termination;
	int stable_ticks;
	int hold_ticks;
	int pulses;
};

class MCPLookAtControl
{
	protected static bool s_Active;
	protected static int s_Generation;
	protected static PlayerBase s_Player;
	protected static vector s_Target;
	protected static float s_BudgetS;
	protected static float s_ElapsedS;
	protected static int s_Ticks;
	protected static int s_Pulses;
	protected static int s_StableTicks;
	protected static int s_HoldTicks;
	protected static float s_InitialError;
	protected static float s_FinalError;
	protected static bool s_HaveInitial;
	protected static bool s_Finished;
	protected static bool s_Converged;
	protected static string s_Termination;
	protected static string s_Pose;
	protected static int s_CommandId;
	// The look the server was sent a copy of and no release for (0: none; a
	// converged look keeps it, EndServerLook), and a release of a sent look
	// that has not left yet, with its id.
	protected static int s_ServerLookId;
	protected static bool s_ServerReleasePending;
	protected static int s_ServerReleaseId;

	protected static const float MAX_CONTROL_S = 3.0;
	protected static const float BAND_DEG = 1.0;
	protected static const int STABLE_TICKS = 3;
	protected static const int HOLD_TICKS = 3;
	protected static const float MAX_PULSE = 0.2;
	// The camera shows an aim pulse a frame or more after it is sent; a fraction of the
	// remaining error per frame converges where a full-error pulse overshoots and oscillates.
	protected static const float GAIN = 0.2;
	// The server's deadman: the look's budget plus this, counted from the
	// start's arrival there. Its band or the owner's release normally ends it
	// first.
	static const float SERVER_DEADMAN_MARGIN_S = 0.5;

	static void Reset()
	{
		s_Active = false;
		s_Player = null;
		s_ElapsedS = 0.0;
		s_Ticks = 0;
		s_Pulses = 0;
		s_StableTicks = 0;
		s_HoldTicks = 0;
		s_InitialError = 0.0;
		s_FinalError = 0.0;
		s_HaveInitial = false;
		s_Finished = false;
		s_Converged = false;
		s_Termination = "";
		s_Pose = "";
		s_CommandId = 0;
	}

	// "" once the look runs (Generation() names it), else input_busy. The
	// server's copy is sent first, as player_move's start (DispatchPlayerMove):
	// a start the input channel cannot take now (taken or full, or a release
	// still waiting for it) leaves both sides as they were, where a look on
	// this side only would be pulled back by the server's reconciliation.
	// Offline the local pulses are the whole look (SendServerStart).
	static string Begin(PlayerBase player, vector target, float budgetS, int commandId)
	{
		int lookId;
		float budget;
		string serverStart;
		budget = budgetS;
		if (budget > MAX_CONTROL_S)
		{
			budget = MAX_CONTROL_S;
		}
		// Release below advances the generation to this id.
		lookId = s_Generation + 1;
		serverStart = SendServerStart(lookId, target, budget);
		if (serverStart == "input_busy")
		{
			return serverStart;
		}
		// The start supersedes there the copy of a converged look still kept
		// (MCPLookAccept), so Release below sends that one no release.
		if (serverStart == "sent")
		{
			s_ServerLookId = 0;
		}
		Release("replaced");
		Reset();
		s_Active = true;
		s_Player = player;
		s_Target = target;
		s_BudgetS = budget;
		s_Pose = "first_idle";
		s_CommandId = commandId;
		if (serverStart == "sent")
		{
			s_ServerLookId = lookId;
		}
		return "";
	}

	static int Generation()
	{
		return s_Generation;
	}

	static bool IsActive()
	{
		return s_Active;
	}

	static bool IsFinished()
	{
		return s_Finished;
	}

	static string PoseRefusal(PlayerBase player)
	{
		return PoseError(player);
	}

	static void Release(string why)
	{
		Disable(s_Player);
		EndServerLook(false);
		if (s_Active && !s_Finished)
		{
			s_Finished = true;
			s_Converged = false;
			s_Termination = why;
		}
		s_Active = false;
		s_Generation = s_Generation + 1;
	}

	// The job's deadline (352f): a pose PoseError refuses ends the look with
	// that code and names it as the pose, not "deadline" with the pose Begin
	// set. A look that already ended keeps its report.
	static void ReleaseAtDeadline()
	{
		string poseError = "";
		if (s_Active && !s_Finished && s_Player)
		{
			poseError = PoseError(s_Player);
		}
		if (poseError == "")
		{
			Release("deadline");
			return;
		}
		s_Pose = poseError;
		Release(poseError);
	}

	static void Disable(PlayerBase player)
	{
		HumanInputController hic;
		if (!player)
		{
			return;
		}
		hic = player.GetInputController();
		if (!hic)
		{
			return;
		}
		hic.OverrideAimChangeX(HumanInputControllerOverrideType.DISABLED, 0.0);
		hic.OverrideAimChangeY(HumanInputControllerOverrideType.DISABLED, 0.0);
	}

	// The owner's cancel (player_look_at_release) of the look its command
	// started. A look that already ended here can still keep its server copy
	// (a converged one, EndServerLook): the cancel sends that copy its
	// release and leaves the report and the generation as they are.
	static void ReleaseOwned(int commandId, string why)
	{
		if (s_CommandId != commandId)
		{
			return;
		}
		if (!s_Active)
		{
			EndServerLook(false);
			return;
		}
		Release(why);
	}

	static void Fill(MCPLookAtReport report)
	{
		if (!report)
		{
			return;
		}
		report.converged = s_Converged;
		report.error_initial_deg = s_InitialError;
		report.error_final_deg = s_FinalError;
		report.ticks = s_Ticks;
		report.duration_s = s_ElapsedS;
		report.pose = s_Pose;
		report.termination = s_Termination;
		report.stable_ticks = s_StableTicks;
		report.hold_ticks = s_HoldTicks;
		report.pulses = s_Pulses;
	}

	static void Finish(string termination, bool converged)
	{
		Disable(s_Player);
		EndServerLook(converged);
		s_Finished = true;
		s_Converged = converged;
		s_Termination = termination;
		s_Active = false;
	}

	// The end of a look the server was sent. A converged look leaves the
	// server's copy to reach its own band, both sides turning toward the same
	// bearing, and keeps its id: a later Release (restore_gameplay) or the
	// owner's cancel (ReleaseOwned) still sends that copy its release, and
	// the next look's start supersedes it.
	// Any other end sends the copy its release, now or from a later command
	// tick (FlushServerRelease). The release names its look, so it never ends
	// a newer one; the server's deadman ends its copy in any case.
	static void EndServerLook(bool converged)
	{
		if (s_ServerLookId <= 0)
		{
			return;
		}
		if (converged)
		{
			return;
		}
		s_ServerReleasePending = true;
		s_ServerReleaseId = s_ServerLookId;
		s_ServerLookId = 0;
		FlushServerRelease();
	}

	// sent, unavailable (not a multiplayer client: offline this one process
	// is the server and the local pulses are the whole look) or input_busy
	// (the input channel is taken or full, gameplay.c:130-131), as
	// MCPPlayerMoveControl.SendStart answers. The release of the previous
	// look goes out first, and while it cannot the start is input_busy too.
	static string SendServerStart(int lookId, vector target, float budgetS)
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
		message.Write(MCP_INPUT_UDT_PLAYER_LOOK);
		message.Write(true);
		message.Write(lookId);
		message.Write(target);
		message.Write(budgetS);
		message.Send();
		return "sent";
	}

	// The release of a sent look goes out as soon as there is a local player
	// and the input channel takes it; until then every command tick retries
	// (OnCommandHandler).
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
		message.Write(MCP_INPUT_UDT_PLAYER_LOOK);
		message.Write(false);
		message.Write(s_ServerReleaseId);
		message.Send();
		s_ServerReleasePending = false;
	}

	// The server copy's values: a finite target and a budget in
	// (0, MAX_CONTROL_S], the bounds DispatchPlayerLookAt and Begin keep.
	static string ServerRequestRefusal(vector target, float budgetS)
	{
		if (!MCPPlayerMoveControl.IsFiniteVector(target))
		{
			return "bad_pos";
		}
		if (!MCPPlayerMoveControl.IsFiniteValue(budgetS))
		{
			return "bad_timeout";
		}
		if (budgetS <= 0.0)
		{
			return "bad_timeout";
		}
		if (budgetS > MAX_CONTROL_S)
		{
			return "bad_timeout";
		}
		return "";
	}

	static bool WithinBand(float errorDeg)
	{
		if (errorDeg > BAND_DEG)
		{
			return false;
		}
		if (errorDeg < -BAND_DEG)
		{
			return false;
		}
		return true;
	}

	// The server copy's pulse for a yaw error in degrees, positive to the
	// right: the owner's gain and clamp (OnCommandHandler), in radians.
	static float ServerPulseX(float errorDeg)
	{
		return ClampPulse(GAIN * errorDeg * Math.DEG2RAD);
	}

	protected static float WrapPi(float angle)
	{
		float pi = 3.14159265;
		while (angle > pi)
		{
			angle = angle - (pi * 2.0);
		}
		while (angle < -pi)
		{
			angle = angle + (pi * 2.0);
		}
		return angle;
	}

	protected static float ClampPulse(float value)
	{
		if (value > MAX_PULSE)
		{
			return MAX_PULSE;
		}
		if (value < -MAX_PULSE)
		{
			return -MAX_PULSE;
		}
		return value;
	}

	protected static bool ReadCamera(PlayerBase player, out vector pos, out vector dir)
	{
		vector rot;
		pos = vector.Zero;
		dir = vector.Zero;
		if (!player)
		{
			return false;
		}
		player.GetCurrentCameraTransform(pos, dir, rot);
		if (dir.Length() <= 0.0)
		{
			return false;
		}
		dir = dir.Normalized();
		return true;
	}

	protected static string PoseError(PlayerBase player)
	{
		HumanInputController hic;
		HumanMovementState state;
		ActionManagerBase actionManager;
		DayZPlayerCamera playerCamera;
		if (!player || !player.IsAlive())
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
			return "in_vehicle";
		}
		if (player.IsInThirdPerson())
		{
			return "third_person";
		}
		// 352f: IsInThirdPerson is the view flag, not the camera on screen
		// (dayzplayerimplement.c:302-305 in 1.30, :299-302 in 1.29). Every
		// third-person camera on foot inherits DayZPlayerCamera3rdPerson
		// (dayzplayercamera3rdperson.c:4 in both). DayZPlayer.GetCurrentCamera
		// (dayzplayer.c:1234 in 1.30, :1216 in 1.29), not the static
		// Camera.GetCurrentCamera of the SUB_BRZ crash.
		playerCamera = player.GetCurrentCamera();
		if (playerCamera && playerCamera.IsInherited(DayZPlayerCamera3rdPerson))
		{
			return "third_person";
		}
		if (player.IsRaised())
		{
			return "weapon_raised";
		}
		if (player.GetCommand_Action() || player.GetCommandModifier_Action())
		{
			return "action_running";
		}
		actionManager = player.GetActionManager();
		if (actionManager && actionManager.GetRunningAction())
		{
			return "action_running";
		}
		if (MCPWeaponControl.IsBusy())
		{
			return "controller_busy";
		}
		hic = player.GetInputController();
		if (!hic)
		{
			return "no_input_controller";
		}
		if (hic.CameraIsFreeLook())
		{
			return "freelook";
		}
		if (hic.CameraIsTracking())
		{
			return "camera_tracking";
		}
		state = new HumanMovementState();
		player.GetMovementState(state);
		if (state.m_iMovement != 0)
		{
			return "moving";
		}
		if (state.m_iStanceIdx != DayZPlayerConstants.STANCEIDX_ERECT && state.m_iStanceIdx != DayZPlayerConstants.STANCEIDX_CROUCH)
		{
			return "bad_stance";
		}
		return "";
	}

	static void OnCommandHandler(PlayerBase player, float pDt)
	{
		vector camPos;
		vector camDir;
		vector desired;
		float dx;
		float dz;
		float dy;
		float horizontal;
		float currentBearing;
		float desiredBearing;
		float currentPitch;
		float desiredPitch;
		float errorDeg;
		float dot;
		float pulseX;
		float pulseY;
		string poseError;
		HumanInputController hic;
		PlayerBase live;
		FlushServerRelease();
		if (!s_Active || s_Finished)
		{
			return;
		}
		if (!GetGame() || GetGame().IsDedicatedServer())
		{
			return;
		}
		live = PlayerBase.Cast(GetGame().GetPlayer());
		if (player != live || player != s_Player)
		{
			Finish("player_changed", false);
			return;
		}
		if (pDt < 0.0)
		{
			Finish("bad_observation", false);
			return;
		}
		s_Ticks = s_Ticks + 1;
		s_ElapsedS = s_ElapsedS + pDt;
		poseError = PoseError(player);
		if (poseError != "")
		{
			s_Pose = poseError;
			Finish(poseError, false);
			return;
		}
		if (s_ElapsedS > s_BudgetS)
		{
			Finish("deadline", false);
			return;
		}
		if (!ReadCamera(player, camPos, camDir))
		{
			Finish("camera_illegible", false);
			return;
		}
		desired = s_Target - camPos;
		if (desired.Length() <= 0.0)
		{
			Finish("bad_pos", false);
			return;
		}
		desired = desired.Normalized();
		dx = camDir[0];
		dy = camDir[1];
		dz = camDir[2];
		horizontal = Math.Sqrt((dx * dx) + (dz * dz));
		currentBearing = Math.Atan2(dx, dz);
		currentPitch = Math.Atan2(dy, horizontal);
		dx = desired[0];
		dy = desired[1];
		dz = desired[2];
		horizontal = Math.Sqrt((dx * dx) + (dz * dz));
		desiredBearing = Math.Atan2(dx, dz);
		desiredPitch = Math.Atan2(dy, horizontal);
		dot = (camDir[0] * desired[0]) + (camDir[1] * desired[1]) + (camDir[2] * desired[2]);
		if (dot > 1.0)
		{
			dot = 1.0;
		}
		if (dot < -1.0)
		{
			dot = -1.0;
		}
		errorDeg = Math.Acos(dot) * Math.RAD2DEG;
		s_FinalError = errorDeg;
		if (!s_HaveInitial)
		{
			s_InitialError = errorDeg;
			s_HaveInitial = true;
		}
		if (errorDeg <= BAND_DEG)
		{
			s_StableTicks = s_StableTicks + 1;
			if (s_StableTicks > STABLE_TICKS)
			{
				s_HoldTicks = s_HoldTicks + 1;
				if (s_HoldTicks >= HOLD_TICKS)
				{
					Finish("converged", true);
				}
			}
			return;
		}
		s_StableTicks = 0;
		s_HoldTicks = 0;
		pulseX = ClampPulse(GAIN * WrapPi(desiredBearing - currentBearing));
		pulseY = ClampPulse(GAIN * WrapPi(desiredPitch - currentPitch));
		hic = player.GetInputController();
		if (!hic)
		{
			Finish("no_input_controller", false);
			return;
		}
		hic.OverrideAimChangeX(HumanInputControllerOverrideType.ONE_FRAME, pulseX);
		hic.OverrideAimChangeY(HumanInputControllerOverrideType.ONE_FRAME, pulseY);
		s_Pulses = s_Pulses + 1;
	}
}

modded class PlayerBase
{
	// Server copy of the owner's player_look_at, per player: the look's id
	// (0: none), its target and deadman, and whether this copy pulsed the aim
	// override. Only the server instance accepts a look, so elsewhere these
	// stay unset and the hooks below cost one test per tick. No ref on
	// entities.
	protected int m_MCPLookId;
	protected vector m_MCPLookTarget;
	protected float m_MCPLookDeadlineS;
	protected bool m_MCPLookPulsed;
	// Pulses from ConsumeMove and from CommandHandler, logged at the end.
	protected int m_MCPLookConsumed;
	protected int m_MCPLookHandled;
	// The highest look id this copy has seen in a start or a release; a start
	// must carry a greater one, as m_MCPMoveLastId keeps for player_move. The
	// owner's ids only grow within its process (MCPLookAtControl.s_Generation
	// is static); a reconnect into this body resets it (OnReconnect).
	protected int m_MCPLookLastId;

	// playerbase.c:6250. Ours stops here, as player_move's does.
	override bool OnInputUserDataProcess(int userDataType, ParamsReadContext ctx)
	{
		if (userDataType == MCP_INPUT_UDT_PLAYER_LOOK)
		{
			MCPLookReadRequest(ctx);
			return true;
		}
		return super.OnInputUserDataProcess(userDataType, ctx);
	}

	// A start (true, its id, its target, its budget) or a release (false, its
	// id), in the order SendServerStart and FlushServerRelease write them.
	protected void MCPLookReadRequest(ParamsReadContext ctx)
	{
		bool isStart;
		int lookId;
		vector target;
		float budgetS;
		isStart = false;
		lookId = 0;
		target = vector.Zero;
		budgetS = 0.0;
		if (!ctx.Read(isStart))
		{
			MCPLookVerdict(false, 0, "read_failed");
			return;
		}
		if (!ctx.Read(lookId))
		{
			MCPLookVerdict(false, 0, "read_failed");
			return;
		}
		if (!isStart)
		{
			MCPLookReadRelease(lookId);
			return;
		}
		if (!ctx.Read(target))
		{
			MCPLookVerdict(false, lookId, "read_failed");
			return;
		}
		if (!ctx.Read(budgetS))
		{
			MCPLookVerdict(false, lookId, "read_failed");
			return;
		}
		MCPLookAccept(lookId, target, budgetS);
	}

	// The owner's release ends the look it names, never a newer one. Its id
	// counts as seen, so a start of that look arriving after it is refused.
	protected void MCPLookReadRelease(int lookId)
	{
		if (lookId > m_MCPLookLastId)
		{
			m_MCPLookLastId = lookId;
		}
		if (m_MCPLookId <= 0)
		{
			MCPLookNote(lookId, "release_without_look");
			return;
		}
		if (m_MCPLookId != lookId)
		{
			MCPLookNote(lookId, "release_of_another_look");
			return;
		}
		MCPLookServerRelease("client");
	}

	// Every check runs before anything changes, as MCPMoveAccept does: the
	// instance, the id's freshness, the owner still being there, the values
	// and the actor. Only an accepted start replaces a look still running.
	protected void MCPLookAccept(int lookId, vector target, float budgetS)
	{
		string refusal;
		// The server instance of this player only (dayzplayer.c:1070-1072, :1171).
		if (GetInstanceType() != DayZPlayerInstanceType.INSTANCETYPE_SERVER)
		{
			MCPLookVerdict(false, lookId, "not_server");
			return;
		}
		if (!GetGame())
		{
			MCPLookVerdict(false, lookId, "no_game");
			return;
		}
		refusal = MCPLookFreshnessRefusal(lookId);
		if (refusal == "")
		{
			// The id is used up now, accepted or not.
			m_MCPLookLastId = lookId;
			refusal = MCPLookOwnerRefusal();
		}
		if (refusal == "")
		{
			refusal = MCPLookAtControl.ServerRequestRefusal(target, budgetS);
		}
		if (refusal == "")
		{
			refusal = MCPPlayerMoveControl.ActorRefusal(this);
		}
		if (refusal != "")
		{
			MCPLookVerdict(false, lookId, refusal);
			return;
		}
		MCPLookServerRelease("superseded");
		m_MCPLookId = lookId;
		m_MCPLookTarget = target;
		m_MCPLookDeadlineS = GetGame().GetTickTime() + budgetS + MCPLookAtControl.SERVER_DEADMAN_MARGIN_S;
		m_MCPLookPulsed = false;
		m_MCPLookConsumed = 0;
		m_MCPLookHandled = 0;
		MCPLookVerdict(true, lookId, "");
	}

	// "" for an id greater than any this copy has seen, else why not.
	protected string MCPLookFreshnessRefusal(int lookId)
	{
		if (lookId <= 0)
		{
			return "bad_look_id";
		}
		if (lookId <= m_MCPLookLastId)
		{
			return "stale_look_id";
		}
		return "";
	}

	// Man.GetIdentity (man.c:21) and vanilla's disconnect flag
	// (playerbase.c:2453-2461), as MCPMoveOwnerRefusal reads them.
	protected string MCPLookOwnerRefusal()
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

	// Every tick of the server's look, from ConsumeMove after super and from
	// CommandHandler: the end checks first (the owner, the actor, its own
	// deadman), then the yaw error from this copy's own position and heading
	// (GetHeadingAngle, the heading players_visibility reads). Within the band
	// the look ends; otherwise one ONE_FRAME pulse.
	protected void MCPLookServerTick(bool fromConsume)
	{
		string refusal;
		HumanInputController hic;
		float compass;
		float bearing;
		float errorDeg;
		if (m_MCPLookId <= 0)
		{
			return;
		}
		if (!GetGame())
		{
			MCPLookServerRelease("no_game");
			return;
		}
		refusal = MCPLookOwnerRefusal();
		if (refusal == "")
		{
			refusal = MCPPlayerMoveControl.ActorRefusal(this);
		}
		if (refusal != "")
		{
			MCPLookServerRelease(refusal);
			return;
		}
		if (GetGame().GetTickTime() > m_MCPLookDeadlineS)
		{
			MCPLookServerRelease("expired");
			return;
		}
		hic = GetInputController();
		compass = MCPPlayerMoveControl.CompassFromHeading(hic.GetHeadingAngle());
		bearing = MCPPlayerMoveControl.BearingDeg(PhysicsGetPositionWS(), m_MCPLookTarget);
		errorDeg = MCPPlayerMoveControl.WrapDeg180(bearing - compass);
		if (MCPLookAtControl.WithinBand(errorDeg))
		{
			MCPLookServerRelease("converged");
			return;
		}
		hic.OverrideAimChangeX(HumanInputControllerOverrideType.ONE_FRAME, MCPLookAtControl.ServerPulseX(errorDeg));
		m_MCPLookPulsed = true;
		if (fromConsume)
		{
			m_MCPLookConsumed = m_MCPLookConsumed + 1;
		}
		else
		{
			m_MCPLookHandled = m_MCPLookHandled + 1;
		}
	}

	// DISABLED only when this copy pulsed (human.c:7-12), then one
	// script-log line with the pulses counted.
	protected void MCPLookServerRelease(string why)
	{
		HumanInputController hic;
		int lookId;
		string line;
		if (m_MCPLookId <= 0)
		{
			return;
		}
		lookId = m_MCPLookId;
		m_MCPLookId = 0;
		if (m_MCPLookPulsed)
		{
			hic = GetInputController();
			if (hic)
			{
				hic.OverrideAimChangeX(HumanInputControllerOverrideType.DISABLED, 0.0);
			}
		}
		m_MCPLookPulsed = false;
		line = "[DayZ_MCP] player_look_at server released look=" + lookId.ToString() + " reason=" + why;
		line = line + " consumed=" + m_MCPLookConsumed.ToString() + " handled=" + m_MCPLookHandled.ToString();
		Print(line);
	}

	// One script-log line per start: the server's own verdict.
	protected void MCPLookVerdict(bool accepted, int lookId, string reason)
	{
		string line;
		line = "[DayZ_MCP] player_look_at server accepted=0";
		if (accepted)
		{
			line = "[DayZ_MCP] player_look_at server accepted=1";
		}
		line = line + " look=" + lookId.ToString() + " reason=" + reason;
		Print(line);
	}

	protected void MCPLookNote(int lookId, string reason)
	{
		Print("[DayZ_MCP] player_look_at server ignored look=" + lookId.ToString() + " reason=" + reason);
	}

	// A client reconnecting into this body (missionserver.c:620-626) may be a
	// new process whose look ids start at 1 again.
	override void OnReconnect()
	{
		super.OnReconnect();
		MCPLookServerRelease("reconnect");
		m_MCPLookLastId = 0;
	}

	// super first. Only the server's copy runs here: the local player's own
	// look runs from MCP_Weapon.c's CommandHandler
	// (MCPLookAtControl.OnCommandHandler).
	override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)
	{
		super.CommandHandler(pDt, pCurrentCommandID, pCurrentCommandFinished);
		if (m_MCPLookId > 0)
		{
			MCPLookServerTick(false);
		}
	}

#ifdef FEATURE_NETWORK_RECONCILIATION
	// Authority (pawn.c:270-276), after super as player_move's ConsumeMove,
	// so the consumed move's own inputs come first.
	protected override event void ConsumeMove(PawnMove pMove)
	{
		super.ConsumeMove(pMove);
		if (m_MCPLookId > 0)
		{
			MCPLookServerTick(true);
		}
	}
#endif
};
