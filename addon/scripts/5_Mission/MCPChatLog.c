// Client mission chat ring buffer for chat_read. Shown lines are recorded in
// ChatLine.Set after Chat.Add filters and splits long messages (chat.c:68-124,
// chatline.c:39). Events that add no line during super.OnEvent are recorded as
// drops from MissionGameplay.OnEvent (missiongameplay.c:765-770).
class MCPChatLog
{
	const int LINE_CAPACITY = 256;
	const int DROP_CAPACITY = 64;
	const int MAX_LINES_DEFAULT = 200;
	const int MAX_LINES_MAX = 500;
	// 32-bit int ceiling (about 68 years at one entry per second); once reached,
	// RecordLine and RecordDrop record nothing more for the rest of the mission.
	const int MAX_SEQ = 2147483647;
	// Bound on /result JSON bytes: one line's estimate is at most
	// 6 * (1024 + 16384) * 4 + 384 = 418176 bytes, below LINE_BYTES_MAX = 491520,
	// so the lines of an answer stay at most 491520 bytes even with the first
	// line always returned (384 covers a line's keys, int/bool fields, five floats
	// at <= 24 characters each and "cut":false, about 346 bytes); 64 drops stay at
	// most 64 * (6 * (64 + 256) * 4 + 192) = 503808 bytes (192 covers a drop's
	// keys, numbers, reason and cut, about 131 bytes); together 995328 bytes, under
	// MAX_BODY_BYTES = 1048576 (tools/dayz_mcp/loopback.py:474, checked at :9313),
	// leaving 53248 bytes for the rest of the result. Vanilla's pieces of text
	// are at most the engine's chat length (chat.c:105-120) and senders are
	// names, so a vanilla line is never cut; any shortened entry sets cut = true.
	const int LINE_SENDER_MAX_CHARS = 1024;
	const int LINE_TEXT_MAX_CHARS = 16384;
	const int DROP_SENDER_MAX_CHARS = 64;
	const int DROP_TEXT_MAX_CHARS = 256;
	const int LINE_BYTES_MAX = 491520;

	protected ref array<ref MCPChatLine> m_Lines;
	protected ref array<ref MCPChatDrop> m_Drops;
	protected int m_LastSeq;
	// Total lines ever recorded in this mission. Monotonic so MissionGameplay.OnEvent
	// detects new lines even after m_Lines saturates at LINE_CAPACITY.
	protected int m_TotalLines;
	// Highest seq ever evicted from either m_Lines or m_Drops, so truncated is true
	// when a drop or a line above the caller's marker has already left its ring.
	protected int m_MaxEvictedSeq;

	void MCPChatLog()
	{
		m_Lines = new array<ref MCPChatLine>();
		m_Drops = new array<ref MCPChatDrop>();
		m_LastSeq = 0;
		m_TotalLines = 0;
		m_MaxEvictedSeq = 0;
	}

	static MCPChatLog Get()
	{
		MissionGameplay mission;
		if (!GetGame())
		{
			return null;
		}
		mission = MissionGameplay.Cast(GetGame().GetMission());
		if (!mission)
		{
			return null;
		}
		return mission.GetMCPChatLog();
	}

	int GetTotalLines()
	{
		return m_TotalLines;
	}

	int GetLastSeq()
	{
		return m_LastSeq;
	}

	void RecordLine(ChatMessageEventParams params)
	{
		MCPChatLine entry;
		MCPChatLine evicted;
		int nowMs;
		string sender;
		string text;
		bool cut;
		if (!params)
		{
			return;
		}
		if (m_LastSeq >= MAX_SEQ)
		{
			return;
		}
		nowMs = 0;
		if (GetGame())
		{
			nowMs = GetGame().GetTime();
		}
		m_LastSeq = m_LastSeq + 1;
		m_TotalLines = m_TotalLines + 1;

		cut = false;
		sender = params.param2;
		if (sender.LengthUtf8() > LINE_SENDER_MAX_CHARS)
		{
			sender = sender.SubstringUtf8(0, LINE_SENDER_MAX_CHARS);
			cut = true;
		}
		text = params.param3;
		if (text.LengthUtf8() > LINE_TEXT_MAX_CHARS)
		{
			text = text.SubstringUtf8(0, LINE_TEXT_MAX_CHARS);
			cut = true;
		}

		entry = new MCPChatLine();
		entry.seq = m_LastSeq;
		entry.t_client_ms = nowMs;
		entry.channel = params.param1;
		entry.sender = sender;
		entry.text = text;
		entry.cut = cut;
		entry.shown_at_ms = -1;

		if (m_Lines.Count() >= LINE_CAPACITY)
		{
			evicted = m_Lines.Get(0);
			if (evicted && evicted.seq > m_MaxEvictedSeq)
			{
				m_MaxEvictedSeq = evicted.seq;
			}
			m_Lines.RemoveOrdered(0);
		}
		m_Lines.Insert(entry);
	}

	// Mirrors MissionGameplay.OnEvent life-state check (missiongameplay.c:765-770)
	// and Chat.Add channel and profile-option filters (chat.c:76-103).
	static string ClassifyDropReason(ChatMessageEventParams params, bool wasAlive)
	{
		int channel;
		if (!wasAlive)
		{
			return "not_alive";
		}
		if (!params)
		{
			return "not_shown";
		}
		channel = params.param1;
		if ((channel & CCSystem) || (channel & CCBattlEye))
		{
			if (g_Game && g_Game.GetProfileOption(EDayZProfilesOptions.GAME_MESSAGES))
			{
				return "filtered_by_option";
			}
		}
		else if (channel & CCAdmin)
		{
			if (g_Game && g_Game.GetProfileOption(EDayZProfilesOptions.ADMIN_MESSAGES))
			{
				return "filtered_by_option";
			}
		}
		else if ((channel & CCDirect) || (channel & CCMegaphone) || (channel & CCTransmitter) || (channel & CCPublicAddressSystem))
		{
			if (g_Game && g_Game.GetProfileOption(EDayZProfilesOptions.PLAYER_MESSAGES))
			{
				return "filtered_by_option";
			}
		}
		else if (channel != 0)
		{
			return "unknown_channel";
		}
		return "not_shown";
	}

	void RecordDrop(ChatMessageEventParams params, bool wasAlive)
	{
		MCPChatDrop entry;
		MCPChatDrop evicted;
		int nowMs;
		string sender;
		string text;
		bool cut;
		if (m_LastSeq >= MAX_SEQ)
		{
			return;
		}
		nowMs = 0;
		if (GetGame())
		{
			nowMs = GetGame().GetTime();
		}
		m_LastSeq = m_LastSeq + 1;

		cut = false;
		entry = new MCPChatDrop();
		entry.seq = m_LastSeq;
		entry.t_client_ms = nowMs;
		entry.channel = 0;
		entry.sender = "";
		entry.text = "";
		if (params)
		{
			sender = params.param2;
			if (sender.LengthUtf8() > DROP_SENDER_MAX_CHARS)
			{
				sender = sender.SubstringUtf8(0, DROP_SENDER_MAX_CHARS);
				cut = true;
			}
			text = params.param3;
			if (text.LengthUtf8() > DROP_TEXT_MAX_CHARS)
			{
				text = text.SubstringUtf8(0, DROP_TEXT_MAX_CHARS);
				cut = true;
			}
			entry.channel = params.param1;
			entry.sender = sender;
			entry.text = text;
		}
		entry.cut = cut;
		entry.reason = ClassifyDropReason(params, wasAlive);

		if (m_Drops.Count() >= DROP_CAPACITY)
		{
			evicted = m_Drops.Get(0);
			if (evicted && evicted.seq > m_MaxEvictedSeq)
			{
				m_MaxEvictedSeq = evicted.seq;
			}
			m_Drops.RemoveOrdered(0);
		}
		m_Drops.Insert(entry);
	}

	// Returns lines (up to maxLines and LINE_BYTES_MAX, always returning the
	// first eligible line) and every drop with seq above marker. When maxLines
	// or LINE_BYTES_MAX cuts m_Lines, response marker is the last returned
	// line's seq so the next read continues from there; drops above the returned
	// marker come again on the next read, each with its seq. Otherwise response
	// marker is m_LastSeq (0 on an empty log, and m_LastSeq when marker exceeds it).
	MCPChatRead Read(int marker, int maxLines)
	{
		MCPChatRead outRead;
		MCPChatLine lineItem;
		MCPChatDrop dropItem;
		int lineIndex;
		int dropIndex;
		int lastLineSeq;
		int lineBytes;
		int itemBytes;
		bool linesCut;

		outRead = new MCPChatRead();
		outRead.truncated = (m_MaxEvictedSeq > marker);

		linesCut = false;
		lastLineSeq = 0;
		lineBytes = 0;
		lineIndex = 0;
		while (lineIndex < m_Lines.Count())
		{
			lineItem = m_Lines.Get(lineIndex);
			if (lineItem && lineItem.seq > marker)
			{
				if (outRead.lines.Count() >= maxLines)
				{
					linesCut = true;
					break;
				}
				itemBytes = 6 * (lineItem.sender.Length() + lineItem.text.Length()) + 384;
				if (outRead.lines.Count() > 0 && lineBytes + itemBytes > LINE_BYTES_MAX)
				{
					linesCut = true;
					break;
				}
				outRead.lines.Insert(lineItem);
				lineBytes = lineBytes + itemBytes;
				lastLineSeq = lineItem.seq;
			}
			lineIndex = lineIndex + 1;
		}

		if (linesCut)
		{
			outRead.marker = lastLineSeq;
		}
		else
		{
			outRead.marker = m_LastSeq;
		}

		dropIndex = 0;
		while (dropIndex < m_Drops.Count())
		{
			dropItem = m_Drops.Get(dropIndex);
			if (dropItem && dropItem.seq > marker)
			{
				outRead.dropped.Insert(dropItem);
			}
			dropIndex = dropIndex + 1;
		}

		FillReadVisibility(outRead);
		return outRead;
	}

	protected ChatLine FindChatLineBySeq(int seq)
	{
		MissionGameplay mission;
		Chat chat;
		array<ref ChatLine> chatLines;
		ChatLine chatLine;
		int idx;
		if (seq <= 0)
		{
			return null;
		}
		if (!GetGame())
		{
			return null;
		}
		mission = MissionGameplay.Cast(GetGame().GetMission());
		if (!mission)
		{
			return null;
		}
		chat = mission.m_Chat;
		if (!chat)
		{
			return null;
		}
		chatLines = chat.GetMCPChatLines();
		if (!chatLines)
		{
			return null;
		}
		idx = 0;
		while (idx < chatLines.Count())
		{
			chatLine = chatLines.Get(idx);
			if (chatLine && chatLine.m_MCPShownSeq == seq)
			{
				return chatLine;
			}
			idx = idx + 1;
		}
		return null;
	}

	void FillReadVisibility(MCPChatRead outRead)
	{
		MissionGameplay mission;
		IngameHud hud;
		Widget hudPanel;
		MCPChatLine lineItem;
		ChatLine chatLine;
		Widget textWidget;
		Widget currentWidget;
		float hierarchyAlpha;
		float sx;
		float sy;
		float sw;
		float sh;
		int lineIdx;
		if (!outRead)
		{
			return;
		}
		outRead.hud_shown = false;
		if (GetGame())
		{
			mission = MissionGameplay.Cast(GetGame().GetMission());
			if (mission)
			{
				hud = IngameHud.Cast(mission.GetHud());
				if (hud)
				{
					hudPanel = hud.GetHudPanelWidget();
					if (hudPanel)
					{
						outRead.hud_shown = hudPanel.IsVisible();
					}
				}
			}
		}
		if (!outRead.lines)
		{
			return;
		}
		lineIdx = 0;
		while (lineIdx < outRead.lines.Count())
		{
			lineItem = outRead.lines.Get(lineIdx);
			if (lineItem)
			{
				chatLine = FindChatLineBySeq(lineItem.seq);
				textWidget = null;
				if (chatLine)
				{
					textWidget = chatLine.m_TextWidget;
				}
				if (textWidget)
				{
					hierarchyAlpha = 1.0;
					currentWidget = textWidget;
					while (currentWidget)
					{
						hierarchyAlpha = hierarchyAlpha * currentWidget.GetAlpha();
						currentWidget = currentWidget.GetParent();
					}
					sx = 0.0;
					sy = 0.0;
					sw = 0.0;
					sh = 0.0;
					textWidget.GetScreenPos(sx, sy);
					textWidget.GetScreenSize(sw, sh);
					lineItem.replaced = false;
					lineItem.visible_hierarchy = textWidget.IsVisibleHierarchy();
					lineItem.alpha = hierarchyAlpha;
					lineItem.screen_x = sx;
					lineItem.screen_y = sy;
					lineItem.screen_w = sw;
					lineItem.screen_h = sh;
				}
				else
				{
					lineItem.replaced = true;
					lineItem.visible_hierarchy = false;
					lineItem.alpha = 0.0;
					lineItem.screen_x = 0.0;
					lineItem.screen_y = 0.0;
					lineItem.screen_w = 0.0;
					lineItem.screen_h = 0.0;
				}
			}
			lineIdx = lineIdx + 1;
		}
	}

	void SampleShownLines(int nowMs)
	{
		MCPChatLine lineItem;
		ChatLine chatLine;
		Widget textWidget;
		Widget currentWidget;
		float hierarchyAlpha;
		int idx;
		if (!m_Lines)
		{
			return;
		}
		idx = m_Lines.Count() - 1;
		while (idx >= 0)
		{
			lineItem = m_Lines.Get(idx);
			if (lineItem)
			{
				if (nowMs - lineItem.t_client_ms > 2000)
				{
					return;
				}
				if (lineItem.shown_at_ms == -1)
				{
					chatLine = FindChatLineBySeq(lineItem.seq);
					textWidget = null;
					if (chatLine)
					{
						textWidget = chatLine.m_TextWidget;
					}
					if (textWidget && textWidget.IsVisibleHierarchy())
					{
						hierarchyAlpha = 1.0;
						currentWidget = textWidget;
						while (currentWidget)
						{
							hierarchyAlpha = hierarchyAlpha * currentWidget.GetAlpha();
							currentWidget = currentWidget.GetParent();
						}
						if (hierarchyAlpha >= 0.9)
						{
							lineItem.shown_at_ms = nowMs;
						}
					}
				}
			}
			idx = idx - 1;
		}
	}
};

modded class Chat
{
	array<ref ChatLine> GetMCPChatLines()
	{
		return m_Lines;
	}
};

modded class ChatLine
{
	int m_MCPShownSeq;

	override void Set(ChatMessageEventParams params)
	{
		MCPChatLog chatLog;
		int linesBefore;
		super.Set(params);
		m_MCPShownSeq = 0;
		chatLog = MCPChatLog.Get();
		if (chatLog)
		{
			linesBefore = chatLog.GetTotalLines();
			chatLog.RecordLine(params);
			if (chatLog.GetTotalLines() != linesBefore)
			{
				m_MCPShownSeq = chatLog.GetLastSeq();
			}
		}
	}
};
