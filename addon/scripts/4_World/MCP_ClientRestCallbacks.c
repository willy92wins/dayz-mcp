// Client REST callbacks that can outlive a mission. Declared in 4_World so
// an in-flight GET or POST completes against a loaded module after the
// Mission module is cleaned. The bridge is reached only through
// MCPClientRestSink; no 5_Mission type is named here.

class MCPClientRestSink
{
	bool IsConnected()
	{
		return false;
	}

	bool IsPollActive(MCPClientPollCallback cb)
	{
		return false;
	}

	void SinkReleaseCallback(RestCallback cb)
	{
	}

	void SinkRecycleResultCallback(MCPClientResultCallback cb)
	{
	}

	void SinkOnPollSuccess(string data, int dataSize)
	{
	}

	void SinkOnPollError(int errorCode)
	{
	}

	void SinkOnPollTimeout()
	{
	}

	void SinkOnResultSuccess(string data, int dataSize)
	{
	}

	void SinkOnResultError(int errorCode)
	{
	}

	void SinkOnResultTimeout()
	{
	}
};

// Static hold for callbacks handed to GET or POST, from that call until a
// terminal event drops them. Drop is idempotent: OnError may repeat.
class MCPClientRestHolder
{
	protected static ref array<ref RestCallback> s_Callbacks;

	static void Hold(RestCallback cb)
	{
		if (!cb)
		{
			return;
		}
		if (!s_Callbacks)
		{
			s_Callbacks = new array<ref RestCallback>();
		}
		int i = 0;
		while (i < s_Callbacks.Count())
		{
			if (s_Callbacks.Get(i) == cb)
			{
				return;
			}
			i = i + 1;
		}
		s_Callbacks.Insert(cb);
	}

	static void Drop(RestCallback cb)
	{
		if (!s_Callbacks || !cb)
		{
			return;
		}
		int i = 0;
		while (i < s_Callbacks.Count())
		{
			if (s_Callbacks.Get(i) == cb)
			{
				s_Callbacks.Remove(i);
				return;
			}
			i = i + 1;
		}
	}

	static int Count()
	{
		if (!s_Callbacks)
		{
			return 0;
		}
		return s_Callbacks.Count();
	}
};

class MCPClientPollCallback : RestCallback
{
	// The sink forwarder is not Managed: detach explicitly; a raw link can dangle.
	protected ref MCPClientRestSink m_Sink;

	void MCPClientPollCallback(MCPClientRestSink sink)
	{
		m_Sink = sink;
	}

	void DetachSink()
	{
		m_Sink = null;
	}

	override void OnSuccess(string data, int dataSize)
	{
		MCPClientRestHolder.Drop(this);
		if (!m_Sink || !m_Sink.IsConnected())
		{
			Print("[MCP-CLIENT] REST poll completed after shutdown: OnSuccess " + dataSize + " bytes, dropped");
			return;
		}
		m_Sink.SinkReleaseCallback(this);
		if (!m_Sink.IsPollActive(this))
		{
			DetachSink();
			return;
		}
		m_Sink.SinkOnPollSuccess(data, dataSize);
	}

	override void OnError(int errorCode)
	{
		MCPClientRestHolder.Drop(this);
		if (!m_Sink || !m_Sink.IsConnected())
		{
			Print("[MCP-CLIENT] REST poll completed after shutdown: OnError code=" + errorCode + ", dropped");
			return;
		}
		m_Sink.SinkReleaseCallback(this);
		if (!m_Sink.IsPollActive(this))
		{
			DetachSink();
			return;
		}
		m_Sink.SinkOnPollError(errorCode);
		DetachSink();
	}

	override void OnTimeout()
	{
		MCPClientRestHolder.Drop(this);
		if (!m_Sink || !m_Sink.IsConnected())
		{
			Print("[MCP-CLIENT] REST poll completed after shutdown: OnTimeout, dropped");
			return;
		}
		m_Sink.SinkReleaseCallback(this);
		if (!m_Sink.IsPollActive(this))
		{
			DetachSink();
			return;
		}
		m_Sink.SinkOnPollTimeout();
		DetachSink();
	}
};

class MCPClientResultCallback : RestCallback
{
	// The sink forwarder is not Managed: detach explicitly; a raw link can dangle.
	protected ref MCPClientRestSink m_Sink;

	void MCPClientResultCallback(MCPClientRestSink sink)
	{
		m_Sink = sink;
	}

	void AttachSink(MCPClientRestSink sink)
	{
		m_Sink = sink;
	}

	void DetachSink()
	{
		m_Sink = null;
	}

	override void OnSuccess(string data, int dataSize)
	{
		MCPClientRestHolder.Drop(this);
		if (!m_Sink || !m_Sink.IsConnected())
		{
			Print("[MCP-CLIENT] REST result completed after shutdown: OnSuccess " + dataSize + " bytes, dropped");
			return;
		}
		m_Sink.SinkReleaseCallback(this);
		m_Sink.SinkOnResultSuccess(data, dataSize);
		// Back to the free list detached: an idle callback holds no sink.
		m_Sink.SinkRecycleResultCallback(this);
		DetachSink();
	}

	override void OnError(int errorCode)
	{
		MCPClientRestHolder.Drop(this);
		if (!m_Sink || !m_Sink.IsConnected())
		{
			Print("[MCP-CLIENT] REST result completed after shutdown: OnError code=" + errorCode + ", dropped");
			return;
		}
		m_Sink.SinkReleaseCallback(this);
		m_Sink.SinkOnResultError(errorCode);
		// OnError may repeat (restapi.c:53): never reuse this identity.
		DetachSink();
	}

	override void OnTimeout()
	{
		MCPClientRestHolder.Drop(this);
		if (!m_Sink || !m_Sink.IsConnected())
		{
			Print("[MCP-CLIENT] REST result completed after shutdown: OnTimeout, dropped");
			return;
		}
		m_Sink.SinkReleaseCallback(this);
		m_Sink.SinkOnResultTimeout();
		DetachSink();
	}
};
