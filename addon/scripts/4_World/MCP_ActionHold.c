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
	protected static bool s_InterruptDebt;
	protected static bool s_StartDebt;
	protected static bool s_EndDebt;
	protected static ref array<ActionManagerClient> s_AckManagers;
	protected static ref array<int> s_AckIds;
	protected static ref array<ActionManagerClient> s_StartedManagers;
	protected static ref array<ActionData> s_StartedData;
	protected static bool s_MCPStartArmed;
	protected static ActionManagerClient s_MCPStartManager;
	protected static ActionBase s_MCPStartAction;
	protected static bool s_StartCaptureArmed;
	protected static ActionManagerClient s_StartCaptureManager;
	protected static ActionBase s_StartCaptureAction;
	protected static ActionData s_StartCapturePrevCurrent;
	protected static bool s_StartCaptureCanSend;
	protected static int s_StartCapturePrevAckId;
	protected static bool s_StartCaptureEnded;
	protected static ref MCPHoldObservation s_Observation;

	static bool IsActive()
	{
		return s_Active;
	}

	// §2.7/§2.9: causal action-input debt on this client process, by wire send type,
	// strictly for MCP-originated sends and actions (ScriptInputUserData.Send is void
	// with no wire ack, gameplay.c:120-131):
	// - MCP ownership: ArmStart/DisarmStart bracket only the two MCP PerformActionStart
	//   call sites (MCPClientBridge.c:4902 for action_hold, :4929 for action_use).
	//   Manual PerformActionStart calls (e.g. radialquickbarmenu.c:763, 767, 775, 779,
	//   793) and direct ActionStart paths leave s_MCPStartArmed false and never create
	//   ack rows, s_StartedData entries, or debt.
	// - START with UseAcknowledgment() true (actionbase.c:1255-1258): ActionStart writes
	//   INPUT_UDT_STANDARD_ACTION_START and ++m_LastAcknowledgmentID into
	//   m_PendingActionAcknowledgmentID before ctx.Send() (actionmanagerclient.c:689-708).
	//   Server reads that id (actionmanagerserver.c:63-70) and replies with
	//   SJ_ACTION_ACK_ACCEPT or SJ_ACTION_ACK_REJECT (dayzplayersyncjunctures.c:12-13,
	//   280-288; actionmanagerserver.c:124-126, 183-188, 203-207, 250, 277-280).
	//   Liquidated per (manager, ackId) only when OnSyncJuncture transitions that
	//   pending ActionData to UA_AM_ACCEPTED or UA_AM_REJECTED (playerbase.c:8502-8506;
	//   actionmanagerbase.c:156-182); otherwise kept until client process exit.
	// - START with UseAcknowledgment() false: ctx.Send() then local Start
	//   (actionmanagerclient.c:708-715), no server ack -> client process exit.
	//   Pre-send rejections (!CanStoreInputUserData at :659-672, !SetupAction at
	//   :674-679, local/offline at :717-722 and :806-815) never reach ctx.Send().
	// - END: EndActionInput (:286-298, armed on manual release at :318) and
	//   RequestEndAction (:278-284) only arm local flags; ProcessActionInputEnd
	//   (:379-402) and ProcessActionRequestEnd (:347-370) send
	//   INPUT_UDT_STANDARD_ACTION_INPUT_END / INPUT_UDT_STANDARD_ACTION_END_REQUEST
	//   with no server ack (actionmanagerserver.c:76-110). Ownership is sampled before
	//   super because EndInput (:400) / EndRequest (:368) can run OnActionEnd() ->
	//   client process exit only when MCP started the action.
	// - Interrupt: RequestInterruptAction (:1341-1350; server :76-98) and
	//   ActionBaseCB.Interrupt (animatedactionbase.c:1, 85-111, reached on !CanContinue
	//   at :426 as well as :407, 445, 451, 500 before OnFinish -> End at :27-54, 488)
	//   send INPUT_UDT_STANDARD_ACTION_END_REQUEST with CMD_ACTIONINT_INTERRUPT and no
	//   server ack -> client process exit when sent on client for an MCP action (from
	//   hold drain/shutdown/force-close, Update at :77-96, ActionManagerBase.Interrupt
	//   at actionmanagerbase.c:302-315, or ActionBaseCB.Interrupt). MCP_HoldLocalEnd
	//   (:827-842) calls OnActionEnd (:830-847; base :329-336) or ActionBase.Interrupt
	//   (actionbase.c:821-830; animatedactionbase.c:377-387 -> Cancel/End), sending nothing.
	static bool InputInFlight()
	{
		if (s_InterruptDebt || s_InterruptSent || s_InterruptPending)
		{
			return true;
		}

		if (s_StartDebt || s_EndDebt)
		{
			return true;
		}

		if (s_AckIds && s_AckIds.Count() > 0)
		{
			return true;
		}

		return false;
	}

	protected static void ClearStartCapture()
	{
		s_StartCaptureArmed = false;
		s_StartCaptureManager = null;
		s_StartCaptureAction = null;
		s_StartCapturePrevCurrent = null;
		s_StartCaptureCanSend = false;
		s_StartCapturePrevAckId = -1;
		s_StartCaptureEnded = false;
	}

	protected static void RecordStartedAction(ActionManagerClient manager, ActionData data)
	{
		if (!manager || !data)
		{
			return;
		}

		if (!s_StartedManagers)
		{
			s_StartedManagers = new array<ActionManagerClient>();
			s_StartedData = new array<ActionData>();
		}

		int i = s_StartedData.Count() - 1;
		while (i >= 0)
		{
			if (s_StartedManagers.Get(i) == manager)
			{
				s_StartedManagers.Remove(i);
				s_StartedData.Remove(i);
			}

			i = i - 1;
		}

		s_StartedManagers.Insert(manager);
		s_StartedData.Insert(data);
	}

	static bool IsStartedAction(ActionManagerClient manager, ActionData data)
	{
		if (!manager || !data)
		{
			return false;
		}

		if (s_Active && manager == s_Manager && data == s_Data)
		{
			return true;
		}

		if (s_StartCaptureArmed && manager == s_StartCaptureManager && data != s_StartCapturePrevCurrent && data.m_Action == s_StartCaptureAction)
		{
			return true;
		}

		if (!s_StartedManagers || !s_StartedData)
		{
			return false;
		}

		int i = 0;
		while (i < s_StartedData.Count())
		{
			if (s_StartedManagers.Get(i) == manager && s_StartedData.Get(i) == data)
			{
				return true;
			}

			i = i + 1;
		}

		return false;
	}

	static bool IsStartedActionData(ActionData data)
	{
		if (!data)
		{
			return false;
		}

		if (s_Active && data == s_Data)
		{
			return true;
		}

		if (s_StartCaptureArmed && data != s_StartCapturePrevCurrent && data.m_Action == s_StartCaptureAction)
		{
			return true;
		}

		if (!s_StartedData)
		{
			return false;
		}

		int i = 0;
		while (i < s_StartedData.Count())
		{
			if (s_StartedData.Get(i) == data)
			{
				return true;
			}

			i = i + 1;
		}

		return false;
	}

	protected static void RecordStartAck(ActionManagerClient manager, int ackId)
	{
		if (!manager || ackId <= 0)
		{
			s_StartDebt = true;
			return;
		}

		if (!s_AckManagers)
		{
			s_AckManagers = new array<ActionManagerClient>();
			s_AckIds = new array<int>();
		}

		int i = 0;
		while (i < s_AckIds.Count())
		{
			if (s_AckManagers.Get(i) == manager && s_AckIds.Get(i) == ackId)
			{
				return;
			}

			i = i + 1;
		}

		s_AckManagers.Insert(manager);
		s_AckIds.Insert(ackId);
	}

	static void ArmStart(ActionManagerClient manager, ActionBase action)
	{
		s_MCPStartArmed = false;
		s_MCPStartManager = null;
		s_MCPStartAction = null;
		ClearStartCapture();
		if (!manager || !action)
		{
			return;
		}

		s_MCPStartArmed = true;
		s_MCPStartManager = manager;
		s_MCPStartAction = action;
	}

	static void DisarmStart()
	{
		s_MCPStartArmed = false;
		s_MCPStartManager = null;
		s_MCPStartAction = null;
	}

	static void BeforeStartCapture(ActionManagerClient manager, ActionBase action)
	{
		if (!s_MCPStartArmed || manager != s_MCPStartManager || action != s_MCPStartAction)
		{
			return;
		}

		DisarmStart();
		ClearStartCapture();
		if (!manager || !action)
		{
			return;
		}

		s_StartCaptureArmed = true;
		s_StartCaptureManager = manager;
		s_StartCaptureAction = action;
		s_StartCapturePrevCurrent = manager.MCP_HoldCurrent();
		s_StartCapturePrevAckId = manager.MCP_PendingAckId();
		if (GetGame() && GetGame().IsMultiplayer() && !action.IsLocal() && s_StartCapturePrevCurrent == null && ScriptInputUserData.CanStoreInputUserData())
		{
			s_StartCaptureCanSend = true;
		}
	}

	static void AfterStartCapture(ActionManagerClient manager, ActionBase action)
	{
		if (!s_StartCaptureArmed || manager != s_StartCaptureManager || action != s_StartCaptureAction)
		{
			return;
		}

		bool canSend = s_StartCaptureCanSend;
		ActionData prevCurrent = s_StartCapturePrevCurrent;
		int prevAckId = s_StartCapturePrevAckId;
		bool endedDuringStart = s_StartCaptureEnded;
		ClearStartCapture();
		if (!manager || !action)
		{
			return;
		}

		ActionData afterCurrent = manager.MCP_HoldCurrent();
		bool startedThis = false;
		if (afterCurrent && afterCurrent != prevCurrent && afterCurrent.m_Action == action)
		{
			startedThis = true;
		}

		if (GetGame() && GetGame().IsMultiplayer() && startedThis)
		{
			RecordStartedAction(manager, afterCurrent);
		}

		if (!canSend)
		{
			return;
		}

		int afterAckId = manager.MCP_PendingAckId();
		if (action.UseAcknowledgment())
		{
			if (afterAckId > 0 && afterAckId != prevAckId)
			{
				RecordStartAck(manager, afterAckId);
				return;
			}

			if (endedDuringStart || startedThis)
			{
				s_StartDebt = true;
			}

			return;
		}

		if (endedDuringStart || startedThis)
		{
			s_StartDebt = true;
		}
	}

	static void AfterSyncJuncture(ActionManagerClient manager, int junctureId, ActionData beforeData, int beforeState, int beforeAckId, ActionData afterData)
	{
		if (!manager || !beforeData || afterData != beforeData || beforeAckId <= 0)
		{
			return;
		}

		if (beforeState != UA_AM_PENDING)
		{
			return;
		}

		bool matched = false;
		if (junctureId == DayZPlayerSyncJunctures.SJ_ACTION_ACK_ACCEPT && afterData.m_State == UA_AM_ACCEPTED)
		{
			matched = true;
		}
		else if (junctureId == DayZPlayerSyncJunctures.SJ_ACTION_ACK_REJECT && afterData.m_State == UA_AM_REJECTED)
		{
			matched = true;
		}

		if (!matched || !s_AckIds || !s_AckManagers)
		{
			return;
		}

		int i = s_AckIds.Count() - 1;
		while (i >= 0)
		{
			if (s_AckManagers.Get(i) == manager && s_AckIds.Get(i) == beforeAckId)
			{
				s_AckManagers.Remove(i);
				s_AckIds.Remove(i);
			}

			i = i - 1;
		}
	}

	static void AfterProcessInputEnd(bool mcpOwned, ActionData beforeData, bool sentBefore, bool sentAfter)
	{
		if (!mcpOwned || sentBefore || !sentAfter)
		{
			return;
		}

		if (!GetGame() || !GetGame().IsMultiplayer())
		{
			return;
		}

		if (!beforeData || !beforeData.m_Action || beforeData.m_Action.IsLocal())
		{
			return;
		}

		s_EndDebt = true;
	}

	static void AfterProcessRequestEnd(bool mcpOwned, ActionData beforeData, bool sentBefore, bool sentAfter)
	{
		if (!mcpOwned || sentBefore || !sentAfter)
		{
			return;
		}

		if (!GetGame() || !GetGame().IsMultiplayer())
		{
			return;
		}

		if (!beforeData || !beforeData.m_Action || beforeData.m_Action.IsLocal())
		{
			return;
		}

		s_EndDebt = true;
	}

	static void AfterRequestInterrupt(ActionManagerClient manager, ActionData beforeData, bool canStore)
	{
		if (!canStore || !manager)
		{
			return;
		}

		if (!GetGame() || !GetGame().IsMultiplayer())
		{
			return;
		}

		if (!IsStartedAction(manager, beforeData))
		{
			return;
		}

		s_InterruptDebt = true;
	}

	static void AfterCallbackInterrupt(bool mcpOwned, bool canSend)
	{
		if (!mcpOwned || !canSend)
		{
			return;
		}

		s_InterruptDebt = true;
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
		if (s_StartCaptureArmed && manager == s_StartCaptureManager)
		{
			ActionData ending = manager.MCP_HoldCurrent();
			if (ending && ending.m_Action == s_StartCaptureAction)
			{
				s_StartCaptureEnded = true;
			}
		}

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
		if (manager && s_StartedManagers && s_StartedData)
		{
			ActionData remaining = manager.MCP_HoldCurrent();
			int i = s_StartedData.Count() - 1;
			while (i >= 0)
			{
				if (s_StartedManagers.Get(i) == manager && s_StartedData.Get(i) != remaining)
				{
					s_StartedManagers.Remove(i);
					s_StartedData.Remove(i);
				}

				i = i - 1;
			}
		}

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
		s_InterruptDebt = true;
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
		if (manager && s_AckManagers && s_AckIds)
		{
			int i = s_AckIds.Count() - 1;
			while (i >= 0)
			{
				if (s_AckManagers.Get(i) == manager)
				{
					s_StartDebt = true;
					s_AckManagers.Remove(i);
					s_AckIds.Remove(i);
				}

				i = i - 1;
			}
		}

		if (manager && s_StartedManagers && s_StartedData)
		{
			int j = s_StartedData.Count() - 1;
			while (j >= 0)
			{
				if (s_StartedManagers.Get(j) == manager)
				{
					s_StartedManagers.Remove(j);
					s_StartedData.Remove(j);
				}

				j = j - 1;
			}
		}

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
				s_InterruptSent = true;
				s_InterruptDebt = true;
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
		s_InterruptDebt = true;
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

// 204e: action_use watches the action it starts and the ActionTarget it made
// for that start, a new one for each command. A local instant action runs its
// Start and then OnActionEnd inside the start call (actionmanagerclient.c:719-721
// in 1.30, :677-679 in 1.29), so no running action is left when
// PerformActionStart returns, as after a start that SetupAction refused. A
// Start of that action with that target during the call means it ran:
// SetupAction keeps the target it is given in m_Target (actionbase.c:174 in
// 1.30, :167 in 1.29). The target is read when the addon's Start is entered,
// before super.Start, so what a Start puts in m_Target after that does not
// change what is counted. An action or mod whose code puts another
// ActionTarget in m_Target before that, or whose Start does not reach the
// addon's (in vanilla only ActionDebug's, which skips super.Start), is not
// seen.
class MCPActionUseWatch
{
	protected static ActionBase s_Watched;
	protected static ref ActionTarget s_WatchedTarget;
	protected static int s_Starts;

	static void Begin(ActionBase action, ActionTarget target)
	{
		s_Watched = action;
		s_WatchedTarget = target;
		s_Starts = 0;
	}

	// The watched Starts since Begin. Nothing is watched after it.
	static int End()
	{
		int starts = s_Starts;
		s_Watched = null;
		s_WatchedTarget = null;
		s_Starts = 0;
		return starts;
	}

	static void NoteStart(ActionBase action, ActionData action_data)
	{
		if (IsWatched(action, action_data))
		{
			s_Starts++;
		}
	}

	protected static bool IsWatched(ActionBase action, ActionData action_data)
	{
		return action == s_Watched && action_data.m_Target == s_WatchedTarget;
	}
};

modded class ActionBase
{
	override void Start(ActionData action_data)
	{
		MCPActionUseWatch.NoteStart(this, action_data);
		super.Start(action_data);
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

	int MCP_PendingAckId()
	{
		return m_PendingActionAcknowledgmentID;
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
		MCPHoldControl.BeforeStartCapture(this, action);
		super.PerformActionStart(action, target, item, extra_data);
		MCPHoldControl.AfterStartCapture(this, action);
		MCPHoldControl.AfterStart(this);
	}

	override void OnSyncJuncture(int pJunctureID, ParamsReadContext pCtx)
	{
		ActionData beforeData = m_CurrentActionData;
		int beforeState = 0;
		if (beforeData)
		{
			beforeState = beforeData.m_State;
		}

		int beforeAckId = m_PendingActionAcknowledgmentID;
		super.OnSyncJuncture(pJunctureID, pCtx);
		MCPHoldControl.AfterSyncJuncture(this, pJunctureID, beforeData, beforeState, beforeAckId, m_CurrentActionData);
	}

	override void ProcessActionInputEnd()
	{
		ActionData beforeData = m_CurrentActionData;
		bool mcpOwned = MCPHoldControl.IsStartedAction(this, beforeData);
		bool sentBefore = m_ActionInputWantEnd_Send;
		super.ProcessActionInputEnd();
		MCPHoldControl.AfterProcessInputEnd(mcpOwned, beforeData, sentBefore, m_ActionInputWantEnd_Send);
	}

	override void ProcessActionRequestEnd()
	{
		ActionData beforeData = m_CurrentActionData;
		bool mcpOwned = MCPHoldControl.IsStartedAction(this, beforeData);
		bool sentBefore = m_ActionWantEndRequest_Send;
		super.ProcessActionRequestEnd();
		MCPHoldControl.AfterProcessRequestEnd(mcpOwned, beforeData, sentBefore, m_ActionWantEndRequest_Send);
	}

	override void RequestInterruptAction()
	{
		bool canStore = false;
		if (GetGame() && ScriptInputUserData.CanStoreInputUserData())
		{
			canStore = true;
		}

		ActionData beforeData = m_CurrentActionData;
		super.RequestInterruptAction();
		MCPHoldControl.AfterRequestInterrupt(this, beforeData, canStore);
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

modded class ActionManagerServer
{
	// Server-side hold snapshot for the lease-close census (§2.7 step 3):
	// reads current/pending without mutating. Named apart from the client
	// helper so source tools see one MCP_HoldBusy (the client's).
	bool MCP_HoldServerBusy()
	{
		if (m_PendingAction)
		{
			return true;
		}

		if (m_CurrentActionData)
		{
			return true;
		}

		return false;
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

modded class ActionBaseCB
{
	protected bool m_MCPOwned;

	override void SetActionData(ActionData action_data)
	{
		super.SetActionData(action_data);
		m_MCPOwned = MCPHoldControl.IsStartedActionData(action_data);
	}

	override void Interrupt()
	{
		bool canSend = false;
		if (GetGame() && !GetGame().IsServer() && GetGame().IsMultiplayer() && ScriptInputUserData.CanStoreInputUserData())
		{
			canSend = true;
		}

		bool mcpOwned = m_MCPOwned;
		if (!mcpOwned && MCPHoldControl.IsStartedActionData(m_ActionData))
		{
			mcpOwned = true;
		}

		super.Interrupt();
		MCPHoldControl.AfterCallbackInterrupt(mcpOwned, canSend);
	}
};
