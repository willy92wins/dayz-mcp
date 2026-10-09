// player_look_at. ONE_FRAME aim overrides measured 2026-10-08: v radians once
// per pulse, positive X turns right (compass increases), positive Y pitches up.
// Success is the measured camera direction, not the commanded pulse.
// The report type lives in World: Mission's MCPLookAt is a later module.

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

	protected static const float MAX_CONTROL_S = 3.0;
	protected static const float BAND_DEG = 1.0;
	protected static const int STABLE_TICKS = 3;
	protected static const int HOLD_TICKS = 3;
	protected static const float MAX_PULSE = 0.2;

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

	static int Begin(PlayerBase player, vector target, float budgetS, int commandId)
	{
		Release("replaced");
		s_Generation = s_Generation + 1;
		Reset();
		s_Active = true;
		s_Player = player;
		s_Target = target;
		s_BudgetS = budgetS;
		if (s_BudgetS > MAX_CONTROL_S)
		{
			s_BudgetS = MAX_CONTROL_S;
		}
		s_Pose = "first_idle";
		s_CommandId = commandId;
		return s_Generation;
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
		if (s_Active && !s_Finished)
		{
			s_Finished = true;
			s_Converged = false;
			s_Termination = why;
		}
		s_Active = false;
		s_Generation = s_Generation + 1;
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

	static void ReleaseOwned(int commandId, string why)
	{
		if (!s_Active || s_CommandId != commandId)
		{
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
		s_Finished = true;
		s_Converged = converged;
		s_Termination = termination;
		s_Active = false;
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
		pulseX = ClampPulse(WrapPi(desiredBearing - currentBearing));
		pulseY = ClampPulse(WrapPi(desiredPitch - currentPitch));
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
