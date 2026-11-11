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
};
