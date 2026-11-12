modded class MissionGameplay
{
	protected ref MCPChatLog m_MCPChatLog;

	void ~MissionGameplay()
	{
		m_MCPChatLog = null;
		MCPClientBridge.ShutdownInstance();
	}

	MCPChatLog GetMCPChatLog()
	{
		if (!m_MCPChatLog)
		{
			m_MCPChatLog = new MCPChatLog();
		}
		return m_MCPChatLog;
	}

	override void OnMissionStart()
	{
		super.OnMissionStart();

		MCPClientBridge bridge = MCPClientBridge.Get();
		if (bridge)
		{
			bridge.OnTick(0.0);
			bridge.ReleaseGameFocus();
		}
	}

	override void OnUpdate(float timeslice)
	{
		// The HUD cursor updates inside super. Stamp the frame first so that
		// update and a later action_cursor read share one tick.
		MCPFrame.Advance();
		super.OnUpdate(timeslice);
		if (m_MCPChatLog && GetGame())
		{
			m_MCPChatLog.SampleShownLines(GetGame().GetTime());
		}

		MCPClientBridge bridge = MCPClientBridge.Get();
		if (bridge)
		{
			bridge.OnTick(timeslice);
		}
	}

	override void OnKeyPress(int key)
	{
		super.OnKeyPress(key);

		MCPClientBridge bridge = MCPClientBridge.Get();
		if (bridge)
		{
			bridge.OnMissionKeyPress(key);
		}
	}

	// Vanilla missiongameplay.c:757-771 forwards ChatMessageEventTypeID to
	// m_Chat.Add only when m_LifeState == EPlayerStates.ALIVE (:765-770).
	// Compare the monotonic GetTotalLines counter across super.OnEvent so a
	// saturated 256-line ring never mistakes a shown line for a drop.
	override void OnEvent(EventType eventTypeId, Param params)
	{
		MCPChatLog chatLog;
		ChatMessageEventParams chatParams;
		int linesBefore;
		bool wasAlive;
		if (eventTypeId == ChatMessageEventTypeID)
		{
			chatLog = GetMCPChatLog();
			linesBefore = chatLog.GetTotalLines();
			wasAlive = (m_LifeState == EPlayerStates.ALIVE);
			super.OnEvent(eventTypeId, params);
			if (chatLog.GetTotalLines() == linesBefore)
			{
				chatParams = ChatMessageEventParams.Cast(params);
				chatLog.RecordDrop(chatParams, wasAlive);
			}
		}
		else
		{
			super.OnEvent(eventTypeId, params);
		}
	}

	// Expose vanilla's protected m_PauseQueued (missiongameplay.c:45) so
	// MCPClientBridge can cancel a pause queued by ui_pause when the menu did
	// not open: Pause() sets it before its loading, respawning and menu
	// visibility guards (:1306, :1308-1326), OnUpdate retries it (:308-311),
	// only opening MENU_INGAME clears it (:1331), and Continue() leaves it
	// (:1334-1348).
	bool MCP_IsPauseQueued()
	{
		return m_PauseQueued;
	}

	void MCP_CancelQueuedPause()
	{
		m_PauseQueued = false;
	}
};
