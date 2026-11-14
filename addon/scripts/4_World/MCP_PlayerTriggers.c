// query_player_state triggers (inbox fa2b): the scripted triggers the
// server-side body is inside now, and its enter/leave/gone events. 4_World:
// the record classes live here because 4_World cannot see 5_Mission, and
// MCPPlayerState (5_Mission) uses them. Only Trigger subclasses fire
// Object.OnEnterTrigger/OnLeaveTrigger (trigger.c:255, :266); a trigger
// deleted while the player is inside never calls OnLeaveTrigger
// (trigger.c:62-67), so a weak reference that reads null becomes a gone
// event, as vanilla treats a null insider (trigger.c:314-317).

//! One trigger the body is inside now, read at enter. Value DTO only:
//! strings, ints, floats and a ref array<float>, so the deep JSON
//! serializer (serializer.c:8) never reaches a live object.
class MCPPlayerTriggerInside
{
	string type;
	int net_id_low;
	int net_id_high;
	ref array<float> pos;
	string shape;
	float enter_uptime_s;

	void MCPPlayerTriggerInside()
	{
		pos = new array<float>();
	}
};

//! One enter, leave or gone event. Value DTO only, like the inside entry.
class MCPPlayerTriggerEvent
{
	int seq;
	string kind;
	string type;
	int net_id_low;
	int net_id_high;
	ref array<float> pos;
	string shape;
	float uptime_s;

	void MCPPlayerTriggerEvent()
	{
		pos = new array<float>();
	}
};

//! Live holder: the weak (non-ref) trigger reference. Never enters
//! MCPPlayerState; only its entry copy does.
class MCPPlayerTriggerLive
{
	ScriptedEntity trigger;
	ref MCPPlayerTriggerInside entry;
};

//! Server-wide run and sequence shared by every body.
class MCPPlayerTriggers
{
	static int s_RunId;
	static int s_LastSeq;

	static int RunId()
	{
		if (s_RunId == 0)
		{
			s_RunId = Math.RandomIntInclusive(1, int.MAX - 1);
		}
		return s_RunId;
	}

	static int NextSeq()
	{
		RunId();
		s_LastSeq = s_LastSeq + 1;
		return s_LastSeq;
	}

	static int LastSeq()
	{
		return s_LastSeq;
	}

	static string ShapeName(TriggerShape shape)
	{
		if (shape == TriggerShape.BOX)
		{
			return "box";
		}
		if (shape == TriggerShape.SPHERE)
		{
			return "sphere";
		}
		if (shape == TriggerShape.CYLINDER)
		{
			return "cylinder";
		}
		return "unknown";
	}
};

//! Per-body record: the triggers inside now and the bounded event ring.
class MCPPlayerTriggerBody
{
	static const int EVENTS_MAX = 32;

	ref array<ref MCPPlayerTriggerLive> inside;
	ref array<ref MCPPlayerTriggerEvent> events;
	int evicted_seq;

	void MCPPlayerTriggerBody()
	{
		inside = new array<ref MCPPlayerTriggerLive>();
		events = new array<ref MCPPlayerTriggerEvent>();
	}

	void RecordEnter(ScriptedEntity trigger)
	{
		MCPPlayerTriggerInside snap;
		MCPPlayerTriggerLive live;
		float now;
		if (!trigger)
		{
			return;
		}
		PruneGone();
		now = GetGame().GetTickTime();
		snap = Snapshot(trigger, now);
		if (FindInside(trigger) < 0)
		{
			live = new MCPPlayerTriggerLive();
			live.trigger = trigger;
			live.entry = snap;
			inside.Insert(live);
		}
		AppendEvent("enter", snap, now);
	}

	void RecordLeave(ScriptedEntity trigger)
	{
		MCPPlayerTriggerInside snap;
		int index;
		float now;
		if (!trigger)
		{
			return;
		}
		PruneGone();
		now = GetGame().GetTickTime();
		snap = Snapshot(trigger, now);
		AppendEvent("leave", snap, now);
		index = FindInside(trigger);
		if (index >= 0)
		{
			inside.RemoveOrdered(index);
		}
	}

	void PruneGone()
	{
		MCPPlayerTriggerLive live;
		int index;
		float now;
		if (!GetGame())
		{
			return;
		}
		now = GetGame().GetTickTime();
		index = 0;
		while (index < inside.Count())
		{
			live = inside.Get(index);
			if (!live.trigger)
			{
				AppendEvent("gone", live.entry, now);
				inside.RemoveOrdered(index);
			}
			else
			{
				index = index + 1;
			}
		}
	}

	protected int FindInside(ScriptedEntity trigger)
	{
		int index;
		index = 0;
		while (index < inside.Count())
		{
			if (inside.Get(index).trigger == trigger)
			{
				return index;
			}
			index = index + 1;
		}
		return -1;
	}

	protected MCPPlayerTriggerInside Snapshot(ScriptedEntity trigger, float now)
	{
		MCPPlayerTriggerInside snap;
		int low;
		int high;
		vector pos;
		low = 0;
		high = 0;
		trigger.GetNetworkID(low, high);
		pos = trigger.GetPosition();
		snap = new MCPPlayerTriggerInside();
		snap.type = trigger.GetType();
		snap.net_id_low = low;
		snap.net_id_high = high;
		snap.pos.Insert(pos[0]);
		snap.pos.Insert(pos[1]);
		snap.pos.Insert(pos[2]);
		snap.shape = MCPPlayerTriggers.ShapeName(trigger.GetTriggerShape());
		snap.enter_uptime_s = now;
		return snap;
	}

	protected void AppendEvent(string kind, MCPPlayerTriggerInside snap, float uptime)
	{
		MCPPlayerTriggerEvent triggerEvent;
		int index;
		triggerEvent = new MCPPlayerTriggerEvent();
		triggerEvent.seq = MCPPlayerTriggers.NextSeq();
		triggerEvent.kind = kind;
		triggerEvent.type = snap.type;
		triggerEvent.net_id_low = snap.net_id_low;
		triggerEvent.net_id_high = snap.net_id_high;
		index = 0;
		while (index < snap.pos.Count())
		{
			triggerEvent.pos.Insert(snap.pos.Get(index));
			index = index + 1;
		}
		triggerEvent.shape = snap.shape;
		triggerEvent.uptime_s = uptime;
		events.Insert(triggerEvent);
		if (events.Count() > EVENTS_MAX)
		{
			evicted_seq = events.Get(0).seq;
			events.RemoveOrdered(0);
		}
	}
};

modded class PlayerBase
{
	protected ref MCPPlayerTriggerBody m_MCPTriggerBody;

	override void OnEnterTrigger(ScriptedEntity trigger)
	{
		super.OnEnterTrigger(trigger);
		MCPRecordEnterTrigger(trigger);
	}

	override void OnLeaveTrigger(ScriptedEntity trigger)
	{
		super.OnLeaveTrigger(trigger);
		MCPRecordLeaveTrigger(trigger);
	}

	void MCPRecordEnterTrigger(ScriptedEntity trigger)
	{
		MCPPlayerTriggerBody bodyState;
		if (!trigger)
		{
			return;
		}
		if (!GetGame())
		{
			return;
		}
		if (!GetGame().IsServer())
		{
			return;
		}
		bodyState = MCPGetTriggerBody(true);
		bodyState.RecordEnter(trigger);
	}

	void MCPRecordLeaveTrigger(ScriptedEntity trigger)
	{
		MCPPlayerTriggerBody bodyState;
		if (!trigger)
		{
			return;
		}
		if (!GetGame())
		{
			return;
		}
		if (!GetGame().IsServer())
		{
			return;
		}
		bodyState = MCPGetTriggerBody(true);
		bodyState.RecordLeave(trigger);
	}

	MCPPlayerTriggerBody MCPGetTriggerBody(bool create)
	{
		if (!m_MCPTriggerBody && create)
		{
			m_MCPTriggerBody = new MCPPlayerTriggerBody();
		}
		return m_MCPTriggerBody;
	}
};
