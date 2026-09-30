// Client action/phase timeline (ficha 5535). 4_World, next to MCP_Weapon.c,
// because a sample reads PlayerBase, its ActionManagerBase and the ItemBase in
// hands. MCPAnimTimelineRead lives here too: View() builds it and 4_World
// cannot see 5_Mission (the same split as MCPVehicleTraceRead). It is not
// vehicle state, so it does not live in MCP_CarScript.c. config.cpp compiles
// the whole 4_World folder.
//
// MissionGameplay.OnUpdate calls MCPClientBridge.OnTick; OnTick calls Tick
// first, before MCPJobRunner and before any early return of the bridge. The
// sampler never runs inside a job.

class MCPAnimTimelineSample
{
	float t_s;
	// True on the extra sample taken on the frame where the running action
	// type, the callback kind or the callback state changed.
	bool edge;
	bool player_present;
	string action;
	int action_state;
	string callback;
	// Both GetCommand_Action and GetCommandModifier_Action were non-null.
	// callback/state then describe the command callback.
	bool callback_both;
	int state;
	string state_name;
	string hands;
	bool hands_present;
	ref array<float> phases;

	void MCPAnimTimelineSample()
	{
		phases = new array<float>();
	}
};

class MCPAnimTimelineRead
{
	string schema;
	string mode;
	string trace_id;
	bool active;
	bool complete;
	bool overflow;
	string stop_reason;
	int sample_hz;
	int capacity;
	int count;
	float elapsed_s;
	int cursor;
	int next_cursor;
	bool eof;
	ref array<string> sources;
	ref array<ref MCPAnimTimelineSample> samples;

	void MCPAnimTimelineRead()
	{
		sources = new array<string>();
		samples = new array<ref MCPAnimTimelineSample>();
	}
};

class MCPAnimTimeline
{
	static const string SCHEMA = "dayz-mcp-anim-timeline-v1";
	static const int SOURCE_NAME_MAX = 64;
	static const int SOURCE_COUNT_MAX = 8;
	static const int SAMPLE_HZ_MIN = 10;
	static const int SAMPLE_HZ_MAX = 60;
	static const int MAX_SAMPLES_MIN = 2;
	static const int MAX_SAMPLES_MAX = 8192;
	static const int LIMIT_MAX = 64;

	static bool s_Active;
	static bool s_Complete;
	static bool s_Overflow;
	static string s_StopReason;
	static string s_LastError;
	static string s_TraceId;
	static int s_SampleHz;
	static int s_Capacity;
	static int s_Count;
	static int s_SourceCount;
	static float s_StartTickS;
	static float s_EndTickS;
	static float s_LastTickS;
	static float s_AccumS;
	static string s_PrevAction;
	static string s_PrevCallback;
	static int s_PrevCallbackState;
	static string s_FrameAction;
	static int s_FrameActionState;
	static string s_FrameCallback;
	static bool s_FrameCallbackBoth;
	static int s_FrameCallbackState;
	static string s_FrameStateName;
	static string s_FrameHands;
	static bool s_FrameHandsPresent;
	static bool s_FramePlayerPresent;
	static ref array<string> s_Sources;
	static ref array<float> s_PhaseScratch;
	static ref array<ref MCPAnimTimelineSample> s_Samples;

	// Up to SOURCE_COUNT_MAX names, each printable ASCII of 1..SOURCE_NAME_MAX
	// characters. An empty list is valid. The same rule as anim_timeline.py.
	static bool SourcesOk(array<string> sourceNames)
	{
		int count;
		int index;
		if (!sourceNames)
		{
			return false;
		}

		count = sourceNames.Count();
		if (count > SOURCE_COUNT_MAX)
		{
			return false;
		}

		index = 0;
		while (index < count)
		{
			if (!IsSourceName(sourceNames.Get(index)))
			{
				return false;
			}
			index = index + 1;
		}
		return true;
	}

	static bool Start(string traceId, int sampleHz, int maxSamples, array<string> sourceNames)
	{
		int index;
		int sourceCount;
		MCPAnimTimelineSample sample;
		s_LastError = "";
		if (s_TraceId != "")
		{
			s_LastError = "trace_exists";
			return false;
		}
		if (sampleHz < SAMPLE_HZ_MIN || sampleHz > SAMPLE_HZ_MAX || maxSamples < MAX_SAMPLES_MIN || maxSamples > MAX_SAMPLES_MAX)
		{
			s_LastError = "bad_args";
			return false;
		}
		if (!SourcesOk(sourceNames))
		{
			s_LastError = "bad_args";
			return false;
		}

		sourceCount = sourceNames.Count();
		s_Sources = new array<string>();
		index = 0;
		while (index < sourceCount)
		{
			s_Sources.Insert(sourceNames.Get(index));
			index = index + 1;
		}
		s_SourceCount = sourceCount;
		s_PhaseScratch = new array<float>();
		s_PhaseScratch.Resize(sourceCount);

		// Preallocated: Tick and CaptureNow only overwrite these slots.
		s_Samples = new array<ref MCPAnimTimelineSample>();
		s_Samples.Resize(maxSamples);
		index = 0;
		while (index < maxSamples)
		{
			sample = new MCPAnimTimelineSample();
			sample.phases.Resize(sourceCount);
			s_Samples.Set(index, sample);
			index = index + 1;
		}

		s_TraceId = traceId;
		s_SampleHz = sampleHz;
		s_Capacity = maxSamples;
		s_Count = 0;
		s_StartTickS = GetGame().GetTickTime();
		s_EndTickS = s_StartTickS;
		s_LastTickS = 0.0;
		s_AccumS = 0.0;
		s_PrevAction = "";
		s_PrevCallback = "";
		s_PrevCallbackState = 0;
		s_Complete = false;
		s_Overflow = false;
		s_StopReason = "";
		s_Active = true;
		return true;
	}

	// Called from MCPClientBridge.OnTick on every client frame. Idle returns
	// before any engine call. No allocation on this path.
	static void Tick(float timeslice)
	{
		float nowS;
		float intervalS;
		bool edge;
		bool due;
		if (!s_Active)
		{
			return;
		}
		if (!GetGame())
		{
			return;
		}

		s_AccumS = s_AccumS + timeslice;
		intervalS = 1.0 / s_SampleHz;
		nowS = GetGame().GetTickTime();
		if (s_Count > 0 && nowS < s_LastTickS)
		{
			Fail("clock_not_monotonic");
			return;
		}
		if (s_Count == 0 && nowS < s_StartTickS)
		{
			Fail("clock_not_monotonic");
			return;
		}
		if (s_Count > 0 && nowS == s_LastTickS)
		{
			return;
		}

		// Read every frame, sampled or not: a change is only an edge on the
		// frame it happens.
		ReadFrame();

		edge = false;
		if (s_Count > 0)
		{
			if (s_FrameAction != s_PrevAction || s_FrameCallback != s_PrevCallback || s_FrameCallbackState != s_PrevCallbackState)
			{
				edge = true;
			}
		}

		due = false;
		if (s_Count == 0 || s_AccumS >= intervalS)
		{
			due = true;
		}
		if (!due && !edge)
		{
			return;
		}
		// An edge that is not due leaves the periodic accumulator alone; both
		// kinds of sample share the same max_samples slots.
		if (due)
		{
			if (s_Count > 0)
			{
				s_AccumS = s_AccumS - intervalS;
			}
			else
			{
				s_AccumS = 0.0;
			}
		}
		CaptureNow(nowS, edge);
	}

	static bool Stop(string traceId)
	{
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return false;
		}
		if (!s_Active)
		{
			return true;
		}
		s_Active = false;
		s_Complete = true;
		s_StopReason = "requested";
		s_EndTickS = GetGame().GetTickTime();
		return true;
	}

	// Discards the trace, active or not. There is no dump to protect, so
	// clear does not ask for stop first (vehicle_trace does: trace_active).
	static bool Clear(string traceId)
	{
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return false;
		}
		ClearState();
		return true;
	}

	static void Abort(string reason)
	{
		if (s_TraceId == "")
		{
			return;
		}
		ClearState();
		s_LastError = reason;
	}

	static bool Matches(string traceId)
	{
		return s_TraceId != "" && s_TraceId == traceId;
	}

	static bool IsActive()
	{
		return s_Active;
	}

	static string GetLastError()
	{
		return s_LastError;
	}

	static MCPAnimTimelineRead View(string mode, string traceId, int cursor, int limit)
	{
		MCPAnimTimelineRead view;
		int index;
		int end;
		float elapsed;
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return null;
		}
		if (cursor < 0 || cursor > s_Count || limit < 1 || limit > LIMIT_MAX)
		{
			s_LastError = "bad_args";
			return null;
		}

		view = new MCPAnimTimelineRead();
		view.schema = SCHEMA;
		view.mode = mode;
		view.trace_id = s_TraceId;
		view.active = s_Active;
		view.complete = s_Complete;
		view.overflow = s_Overflow;
		view.stop_reason = s_StopReason;
		view.sample_hz = s_SampleHz;
		view.capacity = s_Capacity;
		view.count = s_Count;
		// Frozen at stop, overflow or failure; running while active.
		elapsed = s_EndTickS - s_StartTickS;
		if (s_Active)
		{
			elapsed = GetGame().GetTickTime() - s_StartTickS;
		}
		if (elapsed < 0.0)
		{
			elapsed = 0.0;
		}
		view.elapsed_s = elapsed;
		view.cursor = cursor;
		index = 0;
		while (index < s_SourceCount)
		{
			view.sources.Insert(s_Sources.Get(index));
			index = index + 1;
		}

		end = cursor;
		if (mode == "read")
		{
			end = cursor + limit;
			if (end > s_Count)
			{
				end = s_Count;
			}
			index = cursor;
			while (index < end)
			{
				view.samples.Insert(s_Samples.Get(index));
				index = index + 1;
			}
		}
		view.next_cursor = end;
		view.eof = !s_Active && end == s_Count;
		return view;
	}

	// Printable ASCII (codes 32..126), 1..SOURCE_NAME_MAX. Same test as
	// MCPClientBridge.IsPrintableInputName.
	protected static bool IsSourceName(string value)
	{
		int length;
		int index;
		string character;
		int code;
		if (value == "")
		{
			return false;
		}

		length = value.Length();
		if (length > SOURCE_NAME_MAX)
		{
			return false;
		}

		index = 0;
		while (index < length)
		{
			character = value.Substring(index, 1);
			code = character.ToAscii();
			if (code < 32 || code > 126)
			{
				return false;
			}
			index = index + 1;
		}
		return true;
	}

	protected static void ReadFrame()
	{
		int index;
		PlayerBase player;
		ActionManagerBase actionManager;
		ActionBase runningAction;
		HumanCommandActionCallback commandCallback;
		HumanCommandActionCallback modifierCallback;
		HumanCommandActionCallback chosenCallback;
		ItemBase heldItem;
		string chosenName;
		index = 0;
		player = null;
		actionManager = null;
		runningAction = null;
		commandCallback = null;
		modifierCallback = null;
		chosenCallback = null;
		heldItem = null;
		chosenName = "";
		s_FramePlayerPresent = false;
		s_FrameAction = "";
		s_FrameActionState = 0;
		s_FrameCallback = "";
		s_FrameCallbackBoth = false;
		s_FrameCallbackState = 0;
		s_FrameStateName = "";
		s_FrameHands = "";
		s_FrameHandsPresent = false;
		while (index < s_SourceCount)
		{
			s_PhaseScratch.Set(index, 0.0);
			index = index + 1;
		}
		if (!GetGame())
		{
			return;
		}

		// No player (respawn, disconnect): the sample says player_present=false.
		player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player)
		{
			return;
		}
		s_FramePlayerPresent = true;

		actionManager = player.GetActionManager();
		if (actionManager)
		{
			runningAction = actionManager.GetRunningAction();
		}
		if (runningAction)
		{
			s_FrameAction = runningAction.Type().ToString();
			s_FrameActionState = actionManager.GetActionState(runningAction);
		}

		commandCallback = player.GetCommand_Action();
		modifierCallback = player.GetCommandModifier_Action();
		// Both live: the full-body command wins and callback_both says so.
		if (commandCallback)
		{
			chosenCallback = commandCallback;
			chosenName = "command";
			if (modifierCallback)
			{
				s_FrameCallbackBoth = true;
			}
		}
		else if (modifierCallback)
		{
			chosenCallback = modifierCallback;
			chosenName = "modifier";
		}
		if (chosenCallback)
		{
			s_FrameCallback = chosenName;
			s_FrameCallbackState = chosenCallback.GetState();
			s_FrameStateName = HumanCommandActionCallback.GetStateString(s_FrameCallbackState);
		}

		// No item: hands_present=false and every phase stays 0.
		heldItem = player.GetItemInHands();
		if (!heldItem)
		{
			return;
		}
		s_FrameHands = heldItem.GetType();
		s_FrameHandsPresent = true;
		index = 0;
		while (index < s_SourceCount)
		{
			s_PhaseScratch.Set(index, heldItem.GetAnimationPhase(s_Sources.Get(index)));
			index = index + 1;
		}
	}

	protected static void CaptureNow(float nowS, bool edge)
	{
		int index;
		MCPAnimTimelineSample sample;
		if (!s_Active)
		{
			return;
		}
		// Full: stop and flag overflow instead of overwriting, like the
		// vehicle trace. The dropped sample is the one that did not fit.
		if (s_Count >= s_Capacity)
		{
			s_Overflow = true;
			Fail("overflow");
			return;
		}
		if (s_Count > 0 && nowS <= s_LastTickS)
		{
			Fail("clock_not_monotonic");
			return;
		}

		sample = s_Samples.Get(s_Count);
		sample.t_s = nowS - s_StartTickS;
		sample.edge = edge;
		sample.player_present = s_FramePlayerPresent;
		sample.action = s_FrameAction;
		sample.action_state = s_FrameActionState;
		sample.callback = s_FrameCallback;
		sample.callback_both = s_FrameCallbackBoth;
		sample.state = s_FrameCallbackState;
		sample.state_name = s_FrameStateName;
		sample.hands = s_FrameHands;
		sample.hands_present = s_FrameHandsPresent;
		index = 0;
		while (index < s_SourceCount)
		{
			sample.phases.Set(index, s_PhaseScratch.Get(index));
			index = index + 1;
		}

		s_PrevAction = s_FrameAction;
		s_PrevCallback = s_FrameCallback;
		s_PrevCallbackState = s_FrameCallbackState;
		s_LastTickS = nowS;
		s_Count = s_Count + 1;
	}

	protected static void Fail(string reason)
	{
		if (s_TraceId == "")
		{
			return;
		}
		s_Active = false;
		s_Complete = false;
		s_StopReason = reason;
		s_LastError = reason;
		if (GetGame())
		{
			s_EndTickS = GetGame().GetTickTime();
		}
	}

	protected static void ClearState()
	{
		s_Active = false;
		s_Complete = false;
		s_Overflow = false;
		s_StopReason = "";
		s_TraceId = "";
		s_SampleHz = 0;
		s_Capacity = 0;
		s_Count = 0;
		s_SourceCount = 0;
		s_StartTickS = 0.0;
		s_EndTickS = 0.0;
		s_LastTickS = 0.0;
		s_AccumS = 0.0;
		s_PrevAction = "";
		s_PrevCallback = "";
		s_PrevCallbackState = 0;
		s_FrameAction = "";
		s_FrameActionState = 0;
		s_FrameCallback = "";
		s_FrameCallbackBoth = false;
		s_FrameCallbackState = 0;
		s_FrameStateName = "";
		s_FrameHands = "";
		s_FrameHandsPresent = false;
		s_FramePlayerPresent = false;
		if (s_Samples)
		{
			s_Samples.Clear();
			s_Samples = null;
		}
		if (s_Sources)
		{
			s_Sources.Clear();
			s_Sources = null;
		}
		if (s_PhaseScratch)
		{
			s_PhaseScratch.Clear();
			s_PhaseScratch = null;
		}
	}
};
