// bot_start / bot_stop (inbox 120f). One PBO for 1.29 and 1.30: every Bot,
// m_Bot, bot-event and movement-override symbol sits inside DAYZ_1_30,
// ROBOCLIENT and INPUT_OVERRIDE. Those macros are the engine's. This addon
// does not define them. The unguarded path returns bot_unavailable and does
// not initialize a dummy.
class MCPBotSession
{
	int object_id;
	int command_id;
	float deadline_s;
};

class MCPBotControl
{
	protected static ref array<ref MCPBotSession> s_Running;
	protected static ref array<int> s_Initialized;

	static bool Available()
	{
		bool ready = false;
#ifdef DAYZ_1_30
#ifdef ROBOCLIENT
#ifdef INPUT_OVERRIDE
		ready = true;
#endif
#endif
#endif
		return ready;
	}

	static void Tick(MCPBridge bridge, float nowS)
	{
		int index;
		int objectId;
		array<int> due;
		Object target;
		PlayerBase player;
		if (!s_Running || s_Running.Count() == 0)
		{
			return;
		}
		due = new array<int>();
		index = 0;
		while (index < s_Running.Count())
		{
			objectId = s_Running.Get(index).object_id;
			target = null;
			player = null;
			if (bridge)
			{
				target = bridge.RuntimeObjectById(objectId);
			}
			if (!target)
			{
				due.Insert(objectId);
			}
			else
			{
				player = PlayerBase.Cast(target);
				if (!player || !player.IsAlive())
				{
					due.Insert(objectId);
				}
				else if (nowS >= s_Running.Get(index).deadline_s)
				{
					due.Insert(objectId);
				}
			}
			index = index + 1;
		}
		index = 0;
		while (index < due.Count())
		{
			objectId = due.Get(index);
			target = null;
			if (bridge)
			{
				target = bridge.RuntimeObjectById(objectId);
			}
			if (!target)
			{
				Release(bridge, objectId, "deleted");
			}
			else
			{
				player = PlayerBase.Cast(target);
				if (!player || !player.IsAlive())
				{
					Release(bridge, objectId, "death");
				}
				else
				{
					Release(bridge, objectId, "ttl");
				}
			}
			index = index + 1;
		}
	}

	static void ShutdownAll(MCPBridge bridge)
	{
		int index;
		int objectId;
		array<int> ids;
		if (!s_Running)
		{
			return;
		}
		ids = new array<int>();
		index = 0;
		while (index < s_Running.Count())
		{
			ids.Insert(s_Running.Get(index).object_id);
			index = index + 1;
		}
		index = 0;
		while (index < ids.Count())
		{
			objectId = ids.Get(index);
			Release(bridge, objectId, "shutdown");
			index = index + 1;
		}
	}

	static void OnObjectGone(MCPBridge bridge, int objectId)
	{
		Release(bridge, objectId, "deleted");
		if (s_Initialized)
		{
			int goneIndex = s_Initialized.Find(objectId);
			if (goneIndex >= 0)
			{
				s_Initialized.Remove(goneIndex);
			}
		}
	}

	// Lease close (plan S1, §2.7 step 1): read the real FSM state of a bot
	// this process started. "idle" is the only state that clears a bot;
	// a state that cannot be read is "unreadable", never clean.
	static string LeaseCloseBotState(MCPBridge bridge, int objectId)
	{
		if (!bridge)
		{
			return "unreadable";
		}

		Object botTarget = bridge.RuntimeObjectById(objectId);
		if (!botTarget)
		{
			return "idle";
		}

		PlayerBase botPlayer = PlayerBase.Cast(botTarget);
		if (!botPlayer)
		{
			return "idle";
		}

		int fsmState = -1;
#ifdef DAYZ_1_30
#ifdef ROBOCLIENT
#ifdef INPUT_OVERRIDE
		if (!botPlayer.m_Bot)
		{
			return "idle";
		}

		fsmState = botPlayer.m_Bot.MCP_LeaseCloseState();
#endif
#endif
#endif
		if (fsmState == 1)
		{
			return "idle";
		}

		if (fsmState == 0)
		{
			return "busy";
		}

		return "unreadable";
	}

	// Busy while any started bot is running, busy after its stop, or in a
	// state nobody could read. An emptied s_Running alone never clears.
	static bool LeaseCloseBusy(MCPBridge bridge)
	{
		if (AnyRunning())
		{
			return true;
		}

		if (!s_Initialized)
		{
			return false;
		}

		int index = 0;
		while (index < s_Initialized.Count())
		{
			if (LeaseCloseBotState(bridge, s_Initialized.Get(index)) != "idle")
			{
				return true;
			}

			index = index + 1;
		}

		return false;
	}

	static string LeaseCloseResidueReason(MCPBridge bridge)
	{
		if (!s_Initialized)
		{
			return "bot_state_unverified";
		}

		int index = 0;
		while (index < s_Initialized.Count())
		{
			if (LeaseCloseBotState(bridge, s_Initialized.Get(index)) == "unreadable")
			{
				return "bot_state_unverified";
			}

			index = index + 1;
		}

		return "bot_stop_rejected";
	}

	static bool AnyRunning()
	{
		if (!s_Running)
		{
			return false;
		}

		return s_Running.Count() > 0;
	}

	static bool IsRunning(int objectId)
	{
		return FindRunning(objectId) >= 0;
	}

	static bool WasInitialized(int objectId)
	{
		int index;
		if (!s_Initialized)
		{
			return false;
		}
		index = 0;
		while (index < s_Initialized.Count())
		{
			if (s_Initialized.Get(index) == objectId)
			{
				return true;
			}
			index = index + 1;
		}
		return false;
	}

	// Starts the vanilla debug transition. Returns the error token, or "".
	static string Start(MCPBridge bridge, int objectId, string action, float ttlS, float nowS, int commandId, out bool started)
	{
		Object target;
		PlayerBase player;
		PlayerIdentity identity;
		MCPBotSession session;
		started = false;
		if (!Available())
		{
			return "bot_unavailable";
		}
		target = null;
		if (bridge)
		{
			target = bridge.RuntimeObjectById(objectId);
		}
		if (!target)
		{
			return "object_not_found";
		}
		player = PlayerBase.Cast(target);
		if (!player)
		{
			return "not_dummy_player";
		}
		identity = player.GetIdentity();
		if (identity)
		{
			return "not_dummy_player";
		}
		if (!player.IsAlive())
		{
			return "player_dead";
		}
		if (IsRunning(objectId))
		{
			return "bot_busy";
		}
		if (!EnsureBot(player, objectId))
		{
			return "bot_unavailable";
		}
		if (!SubmitStart(player, action, started))
		{
			return "bot_action_rejected";
		}
		if (!s_Running)
		{
			s_Running = new array<ref MCPBotSession>();
		}
		session = new MCPBotSession();
		session.object_id = objectId;
		session.command_id = commandId;
		session.deadline_s = nowS + ttlS;
		s_Running.Insert(session);
		return "";
	}

	// Idempotent for an initialized idle dummy: stopped stays false.
	static string Stop(MCPBridge bridge, int objectId, out bool stopped, out string releasedBy)
	{
		Object target;
		PlayerBase player;
		PlayerIdentity identity;
		stopped = false;
		releasedBy = "";
		if (!Available())
		{
			return "bot_unavailable";
		}
		target = null;
		if (bridge)
		{
			target = bridge.RuntimeObjectById(objectId);
		}
		if (!target)
		{
			return "object_not_found";
		}
		player = PlayerBase.Cast(target);
		if (!player)
		{
			return "not_dummy_player";
		}
		identity = player.GetIdentity();
		if (identity)
		{
			return "not_dummy_player";
		}
		if (!player.IsAlive())
		{
			return "player_dead";
		}
		if (!IsRunning(objectId))
		{
			stopped = false;
			releasedBy = "idle";
			return "";
		}
		Release(bridge, objectId, "stop");
		stopped = true;
		releasedBy = "stop";
		return "";
	}

	protected static void Release(MCPBridge bridge, int objectId, string why)
	{
		int index;
		Object target;
		PlayerBase player;
		index = FindRunning(objectId);
		if (index < 0)
		{
			return;
		}
		s_Running.Remove(index);
		target = null;
		if (bridge)
		{
			target = bridge.RuntimeObjectById(objectId);
		}
		player = PlayerBase.Cast(target);
		if (player)
		{
			SubmitStop(player);
		}
	}

	protected static int FindRunning(int objectId)
	{
		int index;
		if (!s_Running)
		{
			return -1;
		}
		index = 0;
		while (index < s_Running.Count())
		{
			if (s_Running.Get(index) && s_Running.Get(index).object_id == objectId)
			{
				return index;
			}
			index = index + 1;
		}
		return -1;
	}

	protected static void RememberInit(int objectId)
	{
		if (!s_Initialized)
		{
			s_Initialized = new array<int>();
		}
		if (WasInitialized(objectId))
		{
			return;
		}
		s_Initialized.Insert(objectId);
	}

	protected static bool EnsureBot(PlayerBase player, int objectId)
	{
		bool ready = false;
#ifdef DAYZ_1_30
#ifdef ROBOCLIENT
#ifdef INPUT_OVERRIDE
		if (!WasInitialized(objectId))
		{
			player.OnSpawnedFromConsole();
			RememberInit(objectId);
		}
		if (player.m_Bot)
		{
			ready = true;
		}
#endif
#endif
#endif
		return ready;
	}

	protected static bool SubmitStart(PlayerBase player, string action, out bool started)
	{
		bool accepted = false;
		started = false;
#ifdef DAYZ_1_30
#ifdef ROBOCLIENT
#ifdef INPUT_OVERRIDE
		int actionId;
		BotEventStartDebug startEvent;
		actionId = ActionId(action);
		if (actionId < 0 || !player.m_Bot)
		{
			return false;
		}
		startEvent = new BotEventStartDebug(player, null, actionId);
		accepted = player.m_Bot.ProcessEvent(startEvent);
		started = accepted;
		return accepted;
#endif
#endif
#endif
		return false;
	}

	protected static void SubmitStop(PlayerBase player)
	{
#ifdef DAYZ_1_30
#ifdef ROBOCLIENT
#ifdef INPUT_OVERRIDE
		BotEventStop stopEvent;
		if (!player || !player.m_Bot)
		{
			return;
		}
		stopEvent = new BotEventStop(player, null);
		player.m_Bot.ProcessEvent(stopEvent);
#endif
#endif
#endif
	}

	protected static int ActionId(string action)
	{
		int actionId = -1;
#ifdef DAYZ_1_30
#ifdef ROBOCLIENT
#ifdef INPUT_OVERRIDE
		if (action == "PLAYER_BOT_RANDOMIZE_STANCE")
		{
			actionId = EActions.PLAYER_BOT_RANDOMIZE_STANCE;
		}
		else if (action == "PLAYER_BOT_RANDOMIZE_MOVEMENT")
		{
			actionId = EActions.PLAYER_BOT_RANDOMIZE_MOVEMENT;
		}
		else if (action == "PLAYER_BOT_SPAM_USER_ACTIONS")
		{
			actionId = EActions.PLAYER_BOT_SPAM_USER_ACTIONS;
		}
		else if (action == "PLAYER_BOT_TEST_ATTACH_AND_DROP_CYCLE")
		{
			actionId = EActions.PLAYER_BOT_TEST_ATTACH_AND_DROP_CYCLE;
		}
		else if (action == "PLAYER_BOT_TEST_ITEM_MOVE_BACK_AND_FORTH")
		{
			actionId = EActions.PLAYER_BOT_TEST_ITEM_MOVE_BACK_AND_FORTH;
		}
		else if (action == "PLAYER_BOT_TEST_SPAWN_OPEN")
		{
			actionId = EActions.PLAYER_BOT_TEST_SPAWN_OPEN;
		}
		else if (action == "PLAYER_BOT_TEST_SPAWN_OPEN_DESTROY")
		{
			actionId = EActions.PLAYER_BOT_TEST_SPAWN_OPEN_DESTROY;
		}
		else if (action == "PLAYER_BOT_TEST_SPAWN_OPEN_EAT")
		{
			actionId = EActions.PLAYER_BOT_TEST_SPAWN_OPEN_EAT;
		}
		else if (action == "PLAYER_BOT_TEST_SWAP_G2H")
		{
			actionId = EActions.PLAYER_BOT_TEST_SWAP_G2H;
		}
		else if (action == "PLAYER_BOT_TEST_SWAP_INTERNAL")
		{
			actionId = EActions.PLAYER_BOT_TEST_SWAP_INTERNAL;
		}
#endif
#endif
#endif
		return actionId;
	}
};
