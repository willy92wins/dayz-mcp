#ifdef DAYZ_1_30
#ifdef ROBOCLIENT
#ifdef INPUT_OVERRIDE
modded class Bot
{
	// Lease close (plan S1): 1 idle, 0 busy, -1 unreadable.
	// m_ActiveFSM bot.c:22, m_IdleState bot.c:26, GetCurrentState hfsmbase.c:43.
	int MCP_LeaseCloseState()
	{
		if (!m_ActiveFSM)
		{
			return -1;
		}

		BotStateBase current = m_ActiveFSM.GetCurrentState();
		if (!current)
		{
			return -1;
		}

		if (current == m_IdleState)
		{
			return 1;
		}

		return 0;
	}
};
#endif
#endif
#endif
