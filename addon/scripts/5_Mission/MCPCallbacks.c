class MCPPollCallback : RestCallback
{
	// The mission singleton owns the Managed bridge; this is a weak soft link.
	protected MCPBridge m_Bridge;

	void MCPPollCallback(MCPBridge bridge)
	{
		m_Bridge = bridge;
	}

	void DetachBridge()
	{
		m_Bridge = null;
	}

	override void OnSuccess(string data, int dataSize)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			if (!m_Bridge.IsActivePollCallback(this))
			{
				return;
			}
			m_Bridge.OnPollSuccess(data, dataSize);
		}
	}

	override void OnError(int errorCode)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			if (!m_Bridge.IsActivePollCallback(this))
			{
				return;
			}
			m_Bridge.OnPollError(errorCode);
		}
	}

	override void OnTimeout()
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			if (!m_Bridge.IsActivePollCallback(this))
			{
				return;
			}
			m_Bridge.OnPollTimeout();
		}
	}
};

class MCPResultCallback : RestCallback
{
	// The mission singleton owns the Managed bridge; this is a weak soft link.
	protected MCPBridge m_Bridge;

	void MCPResultCallback(MCPBridge bridge)
	{
		m_Bridge = bridge;
	}

	void AttachBridge(MCPBridge bridge)
	{
		m_Bridge = bridge;
	}

	void DetachBridge()
	{
		m_Bridge = null;
	}

	override void OnSuccess(string data, int dataSize)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnResultSuccess(data, dataSize);
			m_Bridge.RecycleResultCallback(this);
			DetachBridge();
		}
	}

	override void OnError(int errorCode)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnResultError(errorCode);
			// OnError may repeat (restapi.c:53): never reuse this identity.
			DetachBridge();
		}
	}

	override void OnTimeout()
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnResultTimeout();
			DetachBridge();
		}
	}
};
