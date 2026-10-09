// action_hold. One continuous action, watched by ActionData identity.
// 4_World so the hooks do not include MCPCommand or MCPResult. The mission
// adapter arms this, then calls PerformActionStart once.

class MCPHoldObservation
{
	int hold_protocol;
	string hold_id;
	bool started;
	string end_state;
	string reason;
	int action_state;
	bool action_state_known;
	float duration_s;
	int completed_cycles;
	string cycles_scope;
	bool placed_known;
	bool already_placed;
	bool flag_restored;
	bool cleanup_complete;
};

class MCPHoldControl
{
	protected static bool s_Active;
	protected static bool s_Ready;
	protected static ActionManagerClient s_Manager;
	protected static PlayerBase s_Player;
	protected static ActionBase s_Action;
	protected static ActionData s_Data;
	protected static ActionData s_PrevPending;
	protected static ActionData s_PrevCurrent;
	protected static bool s_PrevIgnore;
	protected static bool s_FlagRestored;
	protected static string s_HoldId;
	protected static float s_StartS;
	protected static float s_DeadlineS;
	protected static float s_DrainNormalUntilS;
	protected static float s_DrainForceUntilS;
	protected static bool s_TimeoutCause;
	protected static bool s_Terminal;
	protected static string s_EndState;
	protected static string s_Reason;
	protected static int s_ActionState;
	protected static bool s_ActionStateKnown;
	protected static int s_Cycles;
	protected static bool s_PlacedKnown;
	protected static bool s_Placed;
	protected static bool s_Started;
	protected static bool s_InterruptPending;
	protected static bool s_InterruptSent;
	protected static ref MCPHoldObservation s_Observation;

	static bool IsActive()
	{
		return s_Active;
	}

	protected static float NowS()
	{
		if (!GetGame())
		{
			return 0;
		}

		return GetGame().GetTickTime();
	}

	protected static void RestoreFlag()
	{
		if (s_FlagRestored)
		{
			return;
		}

		if (s_Manager)
		{
			s_Manager.SetIgnoreAutomaticInputEnd(s_PrevIgnore);
		}

		s_FlagRestored = true;
	}

	protected static bool DataStillLocal()
	{
		if (!s_Data || !s_Manager)
		{
			return false;
		}

		if (s_Manager.MCP_HoldPending() == s_Data)
		{
			return true;
		}

		if (s_Manager.MCP_HoldCurrent() == s_Data)
		{
			return true;
		}

		return false;
	}

	protected static void Adopt(ActionManagerClient manager)
	{
		if (!s_Active || s_Data || !manager)
		{
			return;
		}

		ActionData pending = manager.MCP_HoldPending();
		ActionData candidate = pending;
		if (!candidate)
		{
			candidate = manager.MCP_HoldCurrent();
		}

		if (!candidate)
		{
			return;
		}

		if (candidate == s_PrevPending)
		{
			return;
		}

		if (candidate == s_PrevCurrent)
		{
			return;
		}

		if (candidate.m_Action != s_Action)
		{
			return;
		}

		s_Data = candidate;
		s_Started = true;
		s_ActionState = candidate.m_State;
		s_ActionStateKnown = true;
	}

	static void Begin(ActionManagerClient manager, PlayerBase player, ActionBase action, string holdId, float holdTimeoutS)
	{
		s_Active = true;
		s_Ready = false;
		s_Manager = manager;
		s_Player = player;
		s_Action = action;
		s_Data = null;
		s_PrevPending = null;
		s_PrevCurrent = null;
		if (manager)
		{
			s_PrevPending = manager.MCP_HoldPending();
			s_PrevCurrent = manager.MCP_HoldCurrent();
			s_PrevIgnore = manager.MCP_HoldIgnore();
		}

		s_FlagRestored = false;
		s_HoldId = holdId;
		s_StartS = NowS();
		s_DeadlineS = s_StartS + holdTimeoutS;
		s_DrainNormalUntilS = 0;
		s_DrainForceUntilS = 0;
		s_TimeoutCause = false;
		s_Terminal = false;
		s_EndState = "";
		s_Reason = "";
		s_ActionState = 0;
		s_ActionStateKnown = false;
		s_Cycles = 0;
		s_PlacedKnown = false;
		s_Placed = false;
		s_Started = false;
		s_InterruptPending = false;
		s_InterruptSent = false;
		s_Observation = null;
		if (manager)
		{
			manager.SetIgnoreAutomaticInputEnd(true);
		}
	}

	static void AfterStart(ActionManagerClient manager)
	{
		if (!s_Active)
		{
			return;
		}

		Adopt(manager);
		if (s_Data)
		{
			s_Started = true;
		}
	}

	static bool HasData()
	{
		if (s_Data)
		{
			return true;
		}

		return false;
	}

	protected static void Capture(ActionData data)
	{
		if (!data)
		{
			return;
		}

		s_ActionState = data.m_State;
		s_ActionStateKnown = true;
		PlaceObjectActionData placeData = PlaceObjectActionData.Cast(data);
		if (placeData)
		{
			s_PlacedKnown = true;
			s_Placed = placeData.m_AlreadyPlaced;
		}

		if (!s_TimeoutCause && NowS() >= s_DeadlineS)
		{
			s_TimeoutCause = true;
		}

		if (s_TimeoutCause)
		{
			s_EndState = "timeout";
			s_Reason = "timeout";
			s_Terminal = true;
			return;
		}

		if (data.m_State == UA_FINISHED)
		{
			s_EndState = "finished";
			s_Reason = "natural";
			s_Terminal = true;
			return;
		}

		if (data.m_State == UA_AM_REJECTED)
		{
			s_EndState = "rejected";
			s_Reason = "server_rejected";
			s_Terminal = true;
			return;
		}

		s_EndState = "cancel";
		if (s_Reason == "")
		{
			s_Reason = "interrupted";
		}

		s_Terminal = true;
	}

	static void BeforeActionEnd(ActionManagerClient manager)
	{
		if (!s_Active || manager != s_Manager)
		{
			return;
		}

		Adopt(manager);
		ActionData current = manager.MCP_HoldCurrent();
		if (!s_Data || current != s_Data)
		{
			return;
		}

		Capture(current);
	}

	static void AfterActionEnd(ActionManagerClient manager)
	{
		if (!s_Active || manager != s_Manager)
		{
			return;
		}

		if (s_Terminal)
		{
			RestoreFlag();
			Publish();
		}
	}

	static void OnProgress(ActionData action_data, CAContinuousBase component)
	{
		if (!s_Active || !action_data || !component)
		{
			return;
		}

		if (!s_Data && s_Manager)
		{
			Adopt(s_Manager);
		}

		if (!s_Data)
		{
			return;
		}

		if (action_data != s_Data)
		{
			return;
		}

		if (action_data.m_ActionComponent != component)
		{
			return;
		}

		s_Cycles = s_Cycles + 1;
	}

	protected static void RememberState()
	{
		if (!s_Manager || !s_Data)
		{
			return;
		}

		if (s_Manager.MCP_HoldCurrent() != s_Data && s_Manager.MCP_HoldPending() != s_Data)
		{
			return;
		}

		s_ActionState = s_Data.m_State;
		s_ActionStateKnown = true;
	}

	protected static bool TransmitInterrupt()
	{
		if (!s_InterruptPending)
		{
			return true;
		}

		if (s_InterruptSent)
		{
			return true;
		}

		// A stored send is not local closure and does not end the drain.
		if (!(GetGame() && ScriptInputUserData.CanStoreInputUserData()))
		{
			return false;
		}

		if (s_Manager)
		{
			s_Manager.RequestInterruptAction();
		}

		s_InterruptSent = true;
		return true;
	}

	protected static void Publish()
	{
		if (s_Ready)
		{
			return;
		}

		if (!s_Terminal)
		{
			return;
		}

		RestoreFlag();
		MCPHoldObservation observation = new MCPHoldObservation();
		observation.hold_protocol = 1;
		observation.hold_id = s_HoldId;
		observation.started = s_Started;
		observation.end_state = s_EndState;
		observation.reason = s_Reason;
		observation.action_state = s_ActionState;
		observation.action_state_known = s_ActionStateKnown;
		observation.duration_s = NowS() - s_StartS;
		if (observation.duration_s < 0)
		{
			observation.duration_s = 0;
		}

		observation.completed_cycles = s_Cycles;
		observation.cycles_scope = "client_progress";
		observation.placed_known = s_PlacedKnown;
		observation.already_placed = s_Placed;
		observation.flag_restored = s_FlagRestored;
		observation.cleanup_complete = false;
		if (s_FlagRestored && !DataStillLocal())
		{
			observation.cleanup_complete = true;
		}

		// An unsent interrupt stays outstanding. Publishing now would set
		// s_Ready and maintenance would stop retrying the send.
		// A send that already landed does not hold the observation back.
		if (s_InterruptPending && !s_InterruptSent && NowS() < s_DrainForceUntilS)
		{
			return;
		}

		s_Observation = observation;
		s_Ready = true;
	}

	static bool Ready()
	{
		return s_Ready;
	}

	static bool PeekCleanupComplete()
	{
		if (!s_Observation)
		{
			return false;
		}

		return s_Observation.cleanup_complete;
	}

	static MCPHoldObservation Take()
	{
		if (!s_Ready)
		{
			return null;
		}

		MCPHoldObservation observation = s_Observation;
		s_Observation = null;
		s_Ready = false;
		s_Active = false;
		s_Manager = null;
		s_Player = null;
		s_Action = null;
		s_Data = null;
		return observation;
	}

	protected static void Finish(string endState, string reason)
	{
		if (!s_Active)
		{
			return;
		}

		if (!s_TimeoutCause)
		{
			s_EndState = endState;
			s_Reason = reason;
		}
		else
		{
			s_EndState = "timeout";
			s_Reason = "timeout";
		}

		s_Terminal = true;
		RestoreFlag();
		Publish();
	}

	static void NoteSetupFailed()
	{
		s_Started = false;
		Finish("rejected", "setup_failed");
	}

	static bool Cancel(string holdId)
	{
		if (!s_Active || holdId != s_HoldId)
		{
			return false;
		}

		if (s_Ready)
		{
			return true;
		}

		if (!s_TimeoutCause)
		{
			s_Reason = "cancel";
			s_EndState = "cancel";
		}

		RestoreFlag();
		s_InterruptPending = true;
		s_DrainNormalUntilS = NowS();
		s_DrainForceUntilS = s_DrainNormalUntilS + 5.0;
		TransmitInterrupt();
		return true;
	}

	static void OnManagerDestroyed(ActionManagerClient manager)
	{
		if (!s_Active || manager != s_Manager)
		{
			return;
		}

		RestoreFlag();
		if (!s_Terminal)
		{
			s_EndState = "cancel";
			s_Reason = "manager_destroyed";
			s_Terminal = true;
		}

		Publish();
	}

	static void Shutdown(string reason)
	{
		if (!s_Active)
		{
			return;
		}

		if (!s_Terminal && !s_TimeoutCause)
		{
			s_EndState = "cancel";
			s_Reason = reason;
			s_Terminal = true;
		}

		RestoreFlag();
		if (s_Manager && s_Data && s_Manager.MCP_HoldCurrent() == s_Data)
		{
			if (GetGame() && ScriptInputUserData.CanStoreInputUserData())
			{
				s_Manager.RequestInterruptAction();
			}

			s_Manager.MCP_HoldLocalEnd(s_Data);
		}

		Publish();
	}

	protected static void RequestForceClose()
	{
		if (!s_Manager || !s_Data)
		{
			return;
		}

		if (s_Manager.MCP_HoldCurrent() != s_Data)
		{
			return;
		}

		if (!(GetGame() && ScriptInputUserData.CanStoreInputUserData()))
		{
			return;
		}

		s_Manager.RequestInterruptAction();
		s_InterruptSent = true;
		s_Manager.MCP_HoldLocalEnd(s_Data);
	}

	static void Maintain()
	{
		if (!s_Active || s_Ready)
		{
			return;
		}

		float nowS = NowS();
		RememberState();
		if (s_InterruptPending)
		{
			// Send, drain deadline and the terminal stay independent.
			// A stored RequestInterruptAction keeps the five-second drain.
			if (!s_InterruptSent)
			{
				TransmitInterrupt();
			}

			if (s_Terminal)
			{
				Publish();
				return;
			}

			if (nowS >= s_DrainForceUntilS)
			{
				if (s_Manager && s_Data && s_Manager.MCP_HoldCurrent() == s_Data)
				{
					s_Manager.MCP_HoldLocalEnd(s_Data);
				}

				if (!s_Terminal)
				{
					if (s_TimeoutCause)
					{
						s_EndState = "timeout";
						s_Reason = "timeout";
					}
					else
					{
						s_EndState = "cancel";
						if (s_Reason == "")
						{
							s_Reason = "cancel";
						}
					}

					s_Terminal = true;
				}

				s_InterruptPending = false;
				Publish();
			}

			return;
		}

		if (!GetGame() || !GetGame().GetPlayer())
		{
			Finish("cancel", "player_changed");
			return;
		}

		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player || player != s_Player)
		{
			Finish("cancel", "player_changed");
			return;
		}

		if (!player.IsAlive())
		{
			Finish("cancel", "player_dead");
			return;
		}

		if (s_Terminal)
		{
			RestoreFlag();
			Publish();
			return;
		}

		if (s_Data && !DataStillLocal())
		{
			Finish("unknown", "lost");
			return;
		}

		if (!s_TimeoutCause && nowS >= s_DeadlineS)
		{
			s_TimeoutCause = true;
			s_EndState = "timeout";
			s_Reason = "timeout";
			RestoreFlag();
			if (s_Manager && s_Data && s_Manager.MCP_HoldCurrent() == s_Data)
			{
				s_Manager.EndActionInput();
			}

			s_DrainNormalUntilS = nowS + 1.0;
			s_DrainForceUntilS = nowS + 5.0;
			s_InterruptPending = true;
		}

		if (!s_TimeoutCause)
		{
			return;
		}

		if (s_Terminal)
		{
			Publish();
			return;
		}

		if (nowS < s_DrainNormalUntilS)
		{
			return;
		}

		if (DataStillLocal())
		{
			RequestForceClose();
			if (s_InterruptPending && nowS < s_DrainForceUntilS)
			{
				return;
			}
		}
		else
		{
			s_Terminal = true;
			Publish();
			return;
		}

		if (nowS >= s_DrainForceUntilS)
		{
			s_Terminal = true;
			Publish();
		}
	}
};

modded class ActionManagerClient
{
	bool MCP_HoldBusy()
	{
		if (m_PendingActionData)
		{
			return true;
		}

		if (m_CurrentActionData)
		{
			return true;
		}

		return false;
	}

	ActionData MCP_HoldPending()
	{
		return m_PendingActionData;
	}

	ActionData MCP_HoldCurrent()
	{
		return m_CurrentActionData;
	}

	bool MCP_HoldIgnore()
	{
		return m_IgnoreAutoInputEnd;
	}

	void MCP_HoldLocalEnd(ActionData data)
	{
		if (!data || m_CurrentActionData != data)
		{
			return;
		}

		if (m_CurrentActionData.m_State == UA_AM_PENDING || m_CurrentActionData.m_State == UA_AM_REJECTED || m_CurrentActionData.m_State == UA_AM_ACCEPTED)
		{
			OnActionEnd();
		}
		else
		{
			m_CurrentActionData.m_Action.Interrupt(m_CurrentActionData);
		}
	}

	override void PerformActionStart(ActionBase action, ActionTarget target, ItemBase item, Param extra_data = NULL)
	{
		super.PerformActionStart(action, target, item, extra_data);
		MCPHoldControl.AfterStart(this);
	}

	override void OnActionEnd()
	{
		MCPHoldControl.BeforeActionEnd(this);
		super.OnActionEnd();
		MCPHoldControl.AfterActionEnd(this);
	}

	void ~ActionManagerClient()
	{
		MCPHoldControl.OnManagerDestroyed(this);
	}
};

modded class CAContinuousBase
{
	override void OnCompletePogress(ActionData action_data)
	{
		MCPHoldControl.OnProgress(action_data, this);
		super.OnCompletePogress(action_data);
	}
};
