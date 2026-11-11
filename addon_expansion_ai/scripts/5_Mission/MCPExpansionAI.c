// Optional Expansion AI adapter (expansion-ai-adapter plan 2-5). This PBO
// only calls public Expansion AI methods and names its classes; it carries
// no Expansion code (CC BY-NC-ND, see docs/expansion-ai-adapter.md). Without
// DayZExpansion_AI_Scripts the game does not load it (config.cpp fails
// closed on requiredAddons), and the base PBO never names these types.

class MCPAIArgsRead
{
	string faction;
	int count;
	ref array<float> waypoints;

	void MCPAIArgsRead()
	{
		waypoints = new array<float>();
	}
};

class MCPAIGroupMember
{
	int object_id;
	string type;
	ref array<float> pos;
	bool loadout_applied;
	ref array<string> inventory;
	bool alive;
	bool gone;
	bool last_observation;
	float health;
	string fsm_state;
	string fsm_substate;
	float threat;
	bool fighting;
	int speed_limit;
	int speed_limit_threat;
	bool emote_playing;
	bool emote_pending;

	void MCPAIGroupMember()
	{
		pos = new array<float>();
		inventory = new array<string>();
	}
};

class MCPAIGroup
{
	bool created;
	int group_id;
	string faction;
	string formation;
	string behaviour;
	string behaviour_expansion;
	string field;
	int living;
	int limit;
	string formation_state;
	int waypoint_count;
	int waypoint_index;
	bool in_combat;
	bool combat_may_override;
	bool roams_expansion_locations;
	ref array<string> allowed;
	ref MCPAIArgsRead args_read;
	string release;
	bool confirmed;
	int released;
	ref array<int> remaining_ids;
	ref array<ref MCPAIGroupMember> members;
	string scene_id;
	ref array<float> current_waypoint;
	int alive;
	int members_total;
	bool group_present;

	void MCPAIGroup()
	{
		allowed = new array<string>();
		remaining_ids = new array<int>();
		members = new array<ref MCPAIGroupMember>();
		current_waypoint = new array<float>();
	}
};

class MCPAIGroupMemberRecord
{
	int object_id;
	eAIBase member;
	string type;
	bool loadout_applied;
	int speed_normal;
	int speed_threat;
	ref array<float> last_pos;
	float last_health;
	bool last_alive;

	void MCPAIGroupMemberRecord()
	{
		last_pos = new array<float>();
	}
};

// One registered group. Records are never removed (plan 2.3): a releasing
// group keeps its member references so the disappearance test can re-read
// them, including rollbacks that never took public ids.
class MCPAIGroupRecord
{
	string spawn_token;
	int group_id;
	// ref: eAIGroup is not Managed, so a plain pointer would dangle once
	// Expansion drops it from s_AllGroups (eAIGroup.c:8, :153, :1009).
	ref eAIGroup group;
	int expansion_id;
	string scene_id;
	string behaviour;
	string group_state;
	ref array<float> waypoints;
	ref array<ref MCPAIGroupMemberRecord> members;

	void MCPAIGroupRecord()
	{
		waypoints = new array<float>();
		members = new array<ref MCPAIGroupMemberRecord>();
	}
};

modded class MCPArgs
{
	string faction;
	string formation;
	string loadout;
	string behaviour;
	string spawn_token;
	int count;
	int group_id;
	ref array<float> waypoints;
};

modded class MCPResult
{
	ref MCPAIGroup ai_group;
	ref array<ref MCPAIGroup> ai_groups;
	bool emote_pending;
};

modded class MCPJob
{
	int emote_gesture_id;
	int emote_member_id;
	bool emote_pending;
	bool emote_interrupted;
};

modded class MCPBridge
{
	protected ref array<ref MCPAIGroupRecord> m_AIGroups;

	protected const int AI_GROUP_COUNT_MAX = 8;
	protected const int AI_TOTAL_MAX = 24;
	protected const int AI_WAYPOINTS_MAX = 32;
	protected const float AI_MEMBER_RING_M = 1.5;

	// Must equal EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH in bridge_readiness.py.
	protected const string EXPANSION_AI_ARG_CONTRACT_HASH = "f1ff4689ba7bc8b2";

	// Adapter verbs as an xai bitmask (bridge_readiness.EXPANSION_AI_VERB_BITS,
	// append only): bit0 ai_group_release, bit1 ai_group_spawn,
	// bit2 ai_group_order, bit3 ai_group_state. The adapter adds no names
	// to caps, so the saturated poll stays within 1024 (#321); the broker
	// expands the mask to names before storing the census.
	protected const string EXPANSION_AI_VERBS = "31";

	protected const float AI_EMOTE_WAIT_MIN_S = 0.5;
	protected const float AI_EMOTE_WAIT_MAX_S = 30.0;

	protected override string ExtensionPollParams()
	{
		return "&xai=" + EXPANSION_AI_ARG_CONTRACT_HASH + "." + EXPANSION_AI_VERBS;
	}

	protected override int DispatchExtension(MCPCommand command, MCPResult result)
	{
		if (command.cmd == "ai_group_release")
		{
			DispatchAIGroupRelease(command, result);
			return 1;
		}

		if (command.cmd == "ai_group_spawn")
		{
			DispatchAIGroupSpawn(command, result);
			return 1;
		}

		if (command.cmd == "ai_group_order")
		{
			DispatchAIGroupOrder(command, result);
			return 1;
		}

		if (command.cmd == "ai_group_state")
		{
			DispatchAIGroupState(command, result);
			return 1;
		}

		if (command.cmd == "ai_emote")
		{
			return DispatchAIEmote(command, result);
		}

		return 0;
	}

	protected override void LeaseCloseExtensionStop()
	{
		int index = 0;

		EnsureAIRegistry();
		while (index < m_AIGroups.Count())
		{
			AIGroupRequestRelease(m_AIGroups.Get(index));
			index = index + 1;
		}
	}

	protected override void LeaseCloseExtensionCensus(MCPLeaseCloseCensus census)
	{
		int index = 0;
		MCPAIGroupRecord record;
		MCPLeaseCloseEntry entry;

		if (!census)
		{
			return;
		}

		EnsureAIRegistry();
		while (index < m_AIGroups.Count())
		{
			record = m_AIGroups.Get(index);
			if (!AIGroupGone(record))
			{
				entry = new MCPLeaseCloseEntry();
				entry.name = "ai_group:" + record.spawn_token;
				entry.state = "residue";
				entry.reason = "ai_group_release_unconfirmed";
				census.entries.Insert(entry);
			}

			index = index + 1;
		}
	}

	protected void EnsureAIRegistry()
	{
		if (!m_AIGroups)
		{
			m_AIGroups = new array<ref MCPAIGroupRecord>();
		}
	}

	protected MCPAIGroupRecord FindAIGroupByToken(string spawnToken)
	{
		int index = 0;
		MCPAIGroupRecord record;

		EnsureAIRegistry();
		while (index < m_AIGroups.Count())
		{
			record = m_AIGroups.Get(index);
			if (record.spawn_token == spawnToken)
			{
				return record;
			}

			index = index + 1;
		}

		return null;
	}

	protected MCPAIGroupRecord FindAIGroupById(int groupId)
	{
		int index = 0;
		MCPAIGroupRecord record;

		EnsureAIRegistry();
		while (index < m_AIGroups.Count())
		{
			record = m_AIGroups.Get(index);
			if (record.group_id == groupId && groupId > 0)
			{
				return record;
			}

			index = index + 1;
		}

		return null;
	}

	// Disappearance test (plan 3.6): no member entity left, and Expansion's
	// own registry no longer returns this exact group for its id. A reused
	// id naming another group does not count, and neither does the absence
	// of the group alone while a member entity remains.
	protected bool AIGroupGone(MCPAIGroupRecord record)
	{
		int index = 0;
		MCPAIGroupMemberRecord memberRecord;

		if (!record)
		{
			return true;
		}

		while (index < record.members.Count())
		{
			memberRecord = record.members.Get(index);
			if (memberRecord.member)
			{
				return false;
			}

			index = index + 1;
		}

		return !AIGroupOriginalPresent(record);
	}

	// The release request of plan 3.6, shared by ai_group_release and the
	// lease-close stop. Deferred deletes only: eAI_Despawn and group Delete
	// finish later, so this never confirms a deletion it only requested.
	protected bool AIGroupRequestRelease(MCPAIGroupRecord record)
	{
		int index = 0;
		MCPAIGroupMemberRecord memberRecord;
		Object registered;

		if (!record)
		{
			return true;
		}

		if (record.group_state == "active")
		{
			while (index < record.members.Count())
			{
				memberRecord = record.members.Get(index);
				if (memberRecord.member)
				{
					memberRecord.member.eAI_Despawn();
				}

				if (memberRecord.object_id > 0 && m_RuntimeObjects && m_RuntimeObjects.Contains(memberRecord.object_id))
				{
					registered = m_RuntimeObjects.Get(memberRecord.object_id);
					if (registered == memberRecord.member)
					{
						MCPBotControl.OnObjectGone(this, memberRecord.object_id);
						m_RuntimeObjects.Remove(memberRecord.object_id);
					}
				}

				index = index + 1;
			}

			if (record.group)
			{
				record.group.Delete();
			}

			record.group_state = "releasing";
		}

		return AIGroupGone(record);
	}

	protected void FillRemainingIds(MCPAIGroupRecord record, MCPAIGroup report)
	{
		int index = 0;
		MCPAIGroupMemberRecord memberRecord;

		while (index < record.members.Count())
		{
			memberRecord = record.members.Get(index);
			if (memberRecord.member && memberRecord.object_id > 0)
			{
				report.remaining_ids.Insert(memberRecord.object_id);
			}

			index = index + 1;
		}
	}

	protected bool AIIsSpawnToken(string value)
	{
		int index = 0;
		int code = 0;
		string character;
		bool allowed = true;

		if (value.Length() != 32)
		{
			return false;
		}

		while (index < 32 && allowed)
		{
			character = value.Substring(index, 1);
			code = character.ToAscii();
			allowed = false;
			if (code >= 48 && code <= 57)
			{
				allowed = true;
			}
			else if (code >= 97 && code <= 102)
			{
				allowed = true;
			}

			index = index + 1;
		}

		return allowed;
	}

	protected string AINormalizeLoadout(string name)
	{
		int nameLength = name.Length();

		if (nameLength > 5 && name.Substring(nameLength - 5, 5) == ".json")
		{
			return name.Substring(0, nameLength - 5);
		}

		return name;
	}

	// ExpansionPrefab.Load returns its prefab even when the JSON does not
	// parse (ExpansionPrefab.c:34-37: JsonLoadFile only logs), so the parse
	// is read from vanilla JsonFileLoader.LoadFile (jsonfileloader.c:7).
	protected bool AILoadoutParses(string path)
	{
		ExpansionPrefab probe = new ExpansionPrefab();
		string parseError;

		return JsonFileLoader<ExpansionPrefab>.LoadFile(path, probe, parseError);
	}

	protected bool AIIsBehaviour(string value)
	{
		if (value == "HOLD")
		{
			return true;
		}

		if (value == "ONCE")
		{
			return true;
		}

		if (value == "LOOP")
		{
			return true;
		}

		if (value == "ROAM")
		{
			return true;
		}

		return false;
	}

	protected bool AIFactionRegistered(typename factionType)
	{
		int index = 0;
		int total = 0;

		if (!factionType)
		{
			return false;
		}

		total = eAIRegisterFaction.s_FactionTypes.Count();
		while (index < total)
		{
			if (eAIRegisterFaction.s_FactionTypes.GetElement(index) == factionType)
			{
				return true;
			}

			index = index + 1;
		}

		return false;
	}

	protected void AIFillFactionAllowed(MCPAIGroup report)
	{
		int index = 0;
		int total = eAIRegisterFaction.s_FactionTypes.Count();
		typename factionType;
		string typeName;

		while (index < total)
		{
			factionType = eAIRegisterFaction.s_FactionTypes.GetElement(index);
			typeName = factionType.ToString();
			report.allowed.Insert(typeName.Substring(10, typeName.Length() - 10));
			index = index + 1;
		}
	}

	// The formations this Expansion build registers (eAIFormation.c:5),
	// named like eAIFormation.GetName() (eAIFormation.c:28): the class name
	// without its "eAIFormation" prefix, so the resolved type is exactly
	// eAIFormation<name>. Shared by spawn and order (plan 3.3).
	protected string AIFormationName(int index)
	{
		string cls = eAIFormation.s_FormationTypes.Get(index).ToString();
		return cls.Substring(12, cls.Length() - 12);
	}

	// The registry, not GetType: GetType answers a random formation for ""
	// and RANDOM (eAIFormation.c:123-128).
	protected bool AIFormationKnown(string formation)
	{
		int index = 0;

		while (index < eAIFormation.s_FormationTypes.Count())
		{
			if (AIFormationName(index) == formation)
			{
				return true;
			}
			index = index + 1;
		}
		return false;
	}

	protected void AIFillFormationAllowed(MCPAIGroup report)
	{
		int index = 0;

		while (index < eAIFormation.s_FormationTypes.Count())
		{
			report.allowed.Insert(AIFormationName(index));
			index = index + 1;
		}
	}

	protected void AIFillBehaviourAllowed(MCPAIGroup report)
	{
		report.allowed.Insert("HOLD");
		report.allowed.Insert("ONCE");
		report.allowed.Insert("LOOP");
		report.allowed.Insert("ROAM");
	}

	// 0..32 finite triples for a spawn, 1..32 for an order (an empty order
	// route would keep the old destination, eAIGroup.c:231-249).
	protected bool AIWaypointsOk(array<float> waypoints, int minPoints)
	{
		int index = 0;

		if (!waypoints)
		{
			return false;
		}
		if (waypoints.Count() < minPoints * 3 || waypoints.Count() > AI_WAYPOINTS_MAX * 3)
		{
			return false;
		}
		while (index < waypoints.Count())
		{
			if (index + 2 >= waypoints.Count())
			{
				return false;
			}
			if (!IsFiniteFloat(waypoints.Get(index)) || !IsFiniteFloat(waypoints.Get(index + 1)) || !IsFiniteFloat(waypoints.Get(index + 2)))
			{
				return false;
			}
			index = index + 3;
		}
		return true;
	}

	protected int AICountLivingMembers()
	{
		int groupIndex = 0;
		int memberIndex = 0;
		int living = 0;
		MCPAIGroupRecord record;
		MCPAIGroupMemberRecord memberRecord;

		EnsureAIRegistry();
		while (groupIndex < m_AIGroups.Count())
		{
			record = m_AIGroups.Get(groupIndex);
			memberIndex = 0;
			while (memberIndex < record.members.Count())
			{
				memberRecord = record.members.Get(memberIndex);
				if (memberRecord.member && memberRecord.member.IsAlive())
				{
					living = living + 1;
				}

				memberIndex = memberIndex + 1;
			}

			groupIndex = groupIndex + 1;
		}

		return living;
	}

	// Plan 3.2 waypoints, always from record.waypoints so the record is
	// the route. ClearWaypoints resets neither the backtracking flag nor
	// the current destination, so both are set here (eAIGroup.c:241-249).
	protected void AIGroupApplyRoute(MCPAIGroupRecord record)
	{
		int index = 0;
		vector waypoint;

		record.group.ClearWaypoints();
		record.group.m_BackTracking = false;
		while (index + 2 < record.waypoints.Count())
		{
			waypoint = Vector(record.waypoints.Get(index), record.waypoints.Get(index + 1), record.waypoints.Get(index + 2));
			record.group.AddWaypoint(waypoint);
			if (index == 0)
			{
				record.group.SetCurrentWaypoint(waypoint);
			}
			index = index + 3;
		}
	}

	// Plan 3.2: HOLD is NONE plus HALT (HALT alone still walks the leader).
	protected void AIGroupApplyMode(MCPAIGroupRecord record)
	{
		if (record.behaviour == "HOLD")
		{
			record.group.SetFormationState(eAIGroupFormationState.NONE);
			record.group.SetWaypointBehaviour(eAIWaypointBehavior.HALT);
			return;
		}
		record.group.SetFormationState(eAIGroupFormationState.IN);
		if (record.behaviour == "ONCE")
		{
			record.group.SetWaypointBehaviour(eAIWaypointBehavior.ONCE);
		}
		else if (record.behaviour == "LOOP")
		{
			record.group.SetWaypointBehaviour(eAIWaypointBehavior.LOOP);
		}
		else
		{
			record.group.SetWaypointBehaviour(eAIWaypointBehavior.ROAMING);
		}
	}

	// Both limits always travel together (plan 3.2, eAIBase.c:5871). In a
	// HOLD the normal limit is STATIC and the threat preference is kept.
	// Only living members receive setters.
	protected void AIGroupApplyLimits(MCPAIGroupRecord record)
	{
		int index = 0;
		MCPAIGroupMemberRecord memberRecord;

		while (index < record.members.Count())
		{
			memberRecord = record.members.Get(index);
			if (memberRecord.member && memberRecord.member.IsAlive())
			{
				if (record.behaviour == "HOLD")
				{
					memberRecord.member.SetMovementSpeedLimits(eAIMovementSpeed.STATIC, memberRecord.speed_threat);
				}
				else
				{
					memberRecord.member.SetMovementSpeedLimits(memberRecord.speed_normal, memberRecord.speed_threat);
				}
			}
			index = index + 1;
		}
	}

	// Read-back translation shared by the spawn and order replies: an
	// unknown Expansion value is named by EnumToString, never guessed.
	protected void AIFillBehaviour(eAIGroup group, MCPAIGroup report)
	{
		eAIWaypointBehavior waypointBehaviour;

		if (!group)
		{
			return;
		}
		waypointBehaviour = group.GetWaypointBehaviour();
		if (waypointBehaviour == eAIWaypointBehavior.HALT)
		{
			report.behaviour = "HOLD";
			report.behaviour_expansion = "HALT";
		}
		else if (waypointBehaviour == eAIWaypointBehavior.ONCE)
		{
			report.behaviour = "ONCE";
			report.behaviour_expansion = "ONCE";
		}
		else if (waypointBehaviour == eAIWaypointBehavior.LOOP)
		{
			report.behaviour = "LOOP";
			report.behaviour_expansion = "LOOP";
		}
		else if (waypointBehaviour == eAIWaypointBehavior.ROAMING)
		{
			report.behaviour = "ROAM";
			report.behaviour_expansion = "ROAMING";
		}
		else
		{
			report.behaviour = "";
			report.behaviour_expansion = typename.EnumToString(eAIWaypointBehavior, waypointBehaviour);
		}
	}

	protected void AIFillGroupReport(MCPAIGroupRecord record, MCPAIGroup report)
	{
		int index = 0;
		int attachIndex = 0;
		int attachCount = 0;
		MCPAIGroupMemberRecord memberRecord;
		MCPAIGroupMember memberReport;
		GameInventory inventory;
		EntityAI attachment;
		vector memberPos;

		report.created = true;
		report.group_id = record.group_id;
		if (record.group)
		{
			if (record.group.GetFaction())
			{
				report.faction = record.group.GetFaction().GetName();
			}

			if (record.group.GetFormation())
			{
				report.formation = record.group.GetFormation().GetName();
			}

			AIFillBehaviour(record.group, report);
		}

		while (index < record.members.Count())
		{
			memberRecord = record.members.Get(index);
			memberReport = new MCPAIGroupMember();
			memberReport.object_id = memberRecord.object_id;
			memberReport.loadout_applied = memberRecord.loadout_applied;
			if (memberRecord.member)
			{
				memberReport.type = memberRecord.member.GetType();
				memberPos = memberRecord.member.GetPosition();
				memberReport.pos.Insert(memberPos[0]);
				memberReport.pos.Insert(memberPos[1]);
				memberReport.pos.Insert(memberPos[2]);
				memberReport.alive = memberRecord.member.IsAlive();
				inventory = memberRecord.member.GetInventory();
				if (inventory)
				{
					attachCount = inventory.AttachmentCount();
					if (attachCount > 16)
					{
						attachCount = 16;
					}

					attachIndex = 0;
					while (attachIndex < attachCount)
					{
						attachment = inventory.GetAttachmentFromIndex(attachIndex);
						if (attachment)
						{
							memberReport.inventory.Insert(attachment.GetType());
						}

						attachIndex = attachIndex + 1;
					}
				}
			}
			else
			{
				memberReport.type = memberRecord.type;
				memberReport.alive = false;
				if (memberRecord.last_pos.Count() == 3)
				{
					memberReport.pos.Insert(memberRecord.last_pos.Get(0));
					memberReport.pos.Insert(memberRecord.last_pos.Get(1));
					memberReport.pos.Insert(memberRecord.last_pos.Get(2));
				}
			}

			report.members.Insert(memberReport);
			index = index + 1;
		}
	}

	protected void AISpawnRefuse(MCPResult result, MCPAIGroup report, string errorName, string fieldName)
	{
		report.created = false;
		report.field = fieldName;
		result.ai_group = report;
		result.ok = false;
		result.error = errorName;
	}

	protected void DispatchAIGroupSpawn(MCPCommand command, MCPResult result)
	{
		MCPAIGroup report = new MCPAIGroup();
		MCPAIArgsRead argsRead = new MCPAIArgsRead();
		MCPArgs args = command.args;
		MCPAIGroupRecord existing;
		MCPAIGroupRecord record;
		MCPAIGroupMemberRecord memberRecord;
		eAIFaction factionInstance;
		eAIGroup group;
		eAIBase member;
		Object spawnedObject;
		typename factionType;
		string loadoutName;
		string loadoutPath;
		string sceneRefusal;
		string memberType;
		vector basePos;
		vector wantedPos;
		vector placementPos;
		float ringX = 1.0;
		float ringZ = 0.0;
		int index = 0;
		int living = 0;
		bool failed = false;

		// args_read is published only on refusals (plan 3.1): the success
		// and repeat paths clear it again below.
		report.args_read = argsRead;
		result.ai_group = report;
		if (!args)
		{
			AISpawnRefuse(result, report, "bad_args", "");
			return;
		}

		argsRead.faction = args.faction;
		argsRead.count = args.count;
		if (args.waypoints)
		{
			index = 0;
			while (index < args.waypoints.Count())
			{
				argsRead.waypoints.Insert(args.waypoints.Get(index));
				index = index + 1;
			}
		}

		// 1. A repeated delivery of the same command creates nothing.
		existing = FindAIGroupByToken(args.spawn_token);
		if (existing)
		{
			if (existing.group_state == "releasing")
			{
				report.created = true;
				result.ok = false;
				result.error = "spawn_failed";
				return;
			}

			report.args_read = null;
			AIFillGroupReport(existing, report);
			result.ok = true;
			return;
		}

		// 2. Shapes, before anything is created.
		if (!AIIsSpawnToken(args.spawn_token))
		{
			AISpawnRefuse(result, report, "bad_args", "spawn_token");
			return;
		}

		if (args.count < 1 || args.count > AI_GROUP_COUNT_MAX)
		{
			AISpawnRefuse(result, report, "bad_args", "count");
			return;
		}

		if (!AIIsBehaviour(args.behaviour))
		{
			AISpawnRefuse(result, report, "bad_args", "behaviour");
			return;
		}

		if (!args.pos || args.pos.Count() != 3)
		{
			AISpawnRefuse(result, report, "bad_args", "pos");
			return;
		}

		if (!IsFiniteFloat(args.pos.Get(0)) || !IsFiniteFloat(args.pos.Get(1)) || !IsFiniteFloat(args.pos.Get(2)))
		{
			AISpawnRefuse(result, report, "bad_args", "pos");
			return;
		}

		if (!AIWaypointsOk(args.waypoints, 0))
		{
			AISpawnRefuse(result, report, "bad_args", "waypoints");
			return;
		}

		// 3. The faction must be registered; never a default fallback.
		factionType = eAIFaction.GetType(args.faction);
		if (!AIFactionRegistered(factionType))
		{
			AIFillFactionAllowed(report);
			AISpawnRefuse(result, report, "bad_args", "faction");
			return;
		}

		factionInstance = eAIFaction.Create(args.faction);
		if (!factionInstance)
		{
			AISpawnRefuse(result, report, "spawn_failed", "");
			return;
		}

		// 4. The formation must be known; Create would fall back to Vee.
		if (!AIFormationKnown(args.formation))
		{
			AIFillFormationAllowed(report);
			AISpawnRefuse(result, report, "bad_args", "formation");
			return;
		}

		// 5. One normalized loadout name for both checks and Apply.
		loadoutName = AINormalizeLoadout(args.loadout);
		if (loadoutName == "")
		{
			loadoutName = AINormalizeLoadout(factionInstance.GetDefaultLoadout());
		}

		// Both args: ExpansionPrefab also has an instance GetPath() (ExpansionPrefab.c:48).
		loadoutPath = ExpansionPrefab.GetPath(loadoutName, EXPANSION_LOADOUT_FOLDER);
		if (!FileExist(loadoutPath))
		{
			AISpawnRefuse(result, report, "bad_args", "loadout");
			return;
		}

		if (!AILoadoutParses(loadoutPath))
		{
			AISpawnRefuse(result, report, "bad_args", "loadout");
			return;
		}

		// 6. The scene cap counts only the members, before creating.
		sceneRefusal = SceneSpawnRefusal(args, args.count);
		if (sceneRefusal == "bad_args")
		{
			AISpawnRefuse(result, report, "bad_args", "scene_id");
			return;
		}

		if (sceneRefusal != "")
		{
			AISpawnRefuse(result, report, sceneRefusal, "");
			return;
		}

		// 7. The live total across every registered group, releasing included.
		living = AICountLivingMembers();
		if (living + args.count > AI_TOTAL_MAX)
		{
			report.living = living;
			report.limit = AI_TOTAL_MAX;
			AISpawnRefuse(result, report, "ai_limit_reached", "");
			return;
		}

		// 8. Create the group and its members.
		group = eAIGroup.CreateGroup(factionInstance);
		if (!group)
		{
			AISpawnRefuse(result, report, "spawn_failed", "");
			return;
		}

		group.m_Persist = false;
		record = new MCPAIGroupRecord();
		record.spawn_token = args.spawn_token;
		record.group = group;
		record.expansion_id = group.GetID();
		record.scene_id = args.scene_id;
		record.behaviour = args.behaviour;
		record.group_state = "active";
		index = 0;
		while (index < args.waypoints.Count())
		{
			record.waypoints.Insert(args.waypoints.Get(index));
			index = index + 1;
		}

		basePos = Vector(args.pos.Get(0), args.pos.Get(1), args.pos.Get(2));
		index = 0;
		while (index < args.count && !failed)
		{
			// Fixed 8-point ring: no division, the translator rejects it.
			ringX = 1.0;
			ringZ = 0.0;
			if (index == 1)
			{
				ringX = 0.7071;
				ringZ = 0.7071;
			}
			else if (index == 2)
			{
				ringX = 0.0;
				ringZ = 1.0;
			}
			else if (index == 3)
			{
				ringX = -0.7071;
				ringZ = 0.7071;
			}
			else if (index == 4)
			{
				ringX = -1.0;
				ringZ = 0.0;
			}
			else if (index == 5)
			{
				ringX = -0.7071;
				ringZ = -0.7071;
			}
			else if (index == 6)
			{
				ringX = 0.0;
				ringZ = -1.0;
			}
			else if (index == 7)
			{
				ringX = 0.7071;
				ringZ = -0.7071;
			}

			wantedPos = Vector(basePos[0] + ringX * AI_MEMBER_RING_M, basePos[1], basePos[2] + ringZ * AI_MEMBER_RING_M);
			placementPos = ExpansionAISpawnBase.GetPlacementPosition(wantedPos);
			memberType = eAISurvivor.GetQuasiRandom();
			spawnedObject = GetGame().CreateObject(memberType, placementPos);
			member = null;
			if (spawnedObject)
			{
				member = eAIBase.Cast(spawnedObject);
			}

			if (!member)
			{
				if (spawnedObject)
				{
					GetGame().ObjectDelete(spawnedObject);
				}

				failed = true;
			}
			else
			{
				memberRecord = new MCPAIGroupMemberRecord();
				memberRecord.member = member;
				memberRecord.type = member.GetType();
				memberRecord.loadout_applied = ExpansionHumanLoadout.Apply(member, loadoutName, true);
				member.SetGroup(group);
				member.eAI_SetLootingBehavior(eAILootingBehavior.NONE);
				memberRecord.speed_normal = member.GetMovementSpeedLimit();
				memberRecord.speed_threat = member.GetMovementSpeedLimitUnderThreat();
				memberRecord.last_pos.Insert(member.GetPosition()[0]);
				memberRecord.last_pos.Insert(member.GetPosition()[1]);
				memberRecord.last_pos.Insert(member.GetPosition()[2]);
				memberRecord.last_health = member.GetHealth();
				memberRecord.last_alive = member.IsAlive();
				record.members.Insert(memberRecord);
			}

			index = index + 1;
		}

		if (failed)
		{
			index = 0;
			while (index < record.members.Count())
			{
				memberRecord = record.members.Get(index);
				if (memberRecord.member)
				{
					memberRecord.member.eAI_Despawn();
				}

				index = index + 1;
			}

			group.Delete();
			record.group_state = "releasing";
			EnsureAIRegistry();
			m_AIGroups.Insert(record);
			report.created = true;
			result.ok = false;
			result.error = "spawn_failed";
			return;
		}

		group.SetFormation(eAIFormation.Create(args.formation));
		AIGroupApplyRoute(record);
		AIGroupApplyMode(record);
		AIGroupApplyLimits(record);

		// 9. Register: group id first, then the members in creation order.
		EnsureAIRegistry();
		m_AIGroups.Insert(record);
		record.group_id = m_NextBatchObjectId;
		m_NextBatchObjectId = m_NextBatchObjectId + 1;
		index = 0;
		while (index < record.members.Count())
		{
			memberRecord = record.members.Get(index);
			memberRecord.object_id = m_NextBatchObjectId;
			m_NextBatchObjectId = m_NextBatchObjectId + 1;
			m_RuntimeObjects.Insert(memberRecord.object_id, memberRecord.member);
			if (args.scene_id != "")
			{
				AddSceneEntry(args.scene_id, memberRecord.object_id, memberRecord.member);
			}

			index = index + 1;
		}

		report.args_read = null;
		AIFillGroupReport(record, report);
		result.ok = true;
	}


	protected void AIOrderSaveSpeed(MCPAIGroupRecord record, int speedValue)
	{
		int index = 0;
		MCPAIGroupMemberRecord memberRecord;

		while (index < record.members.Count())
		{
			memberRecord = record.members.Get(index);
			if (memberRecord.member && memberRecord.member.IsAlive())
			{
				memberRecord.speed_normal = speedValue;
			}
			index = index + 1;
		}
	}

	// Order-only read-back fields, on top of the shared AIFillBehaviour:
	// reusable by ai_group_state (plan 3.4). One row per registered
	// member; limits are 0 when the entity is gone.
	protected void AIFillGroupOrderFields(MCPAIGroupRecord record, MCPAIGroup report)
	{
		int index = 0;
		MCPAIGroupMemberRecord memberRecord;
		MCPAIGroupMember memberReport;
		eAIFormation formation;
		eAIGroupFormationState formationState;


		report.group_id = record.group_id;
		formation = record.group.GetFormation();
		if (formation)
		{
			report.formation = formation.GetName();
		}
		AIFillBehaviour(record.group, report);
		if (report.behaviour == "ROAM")
		{
			report.roams_expansion_locations = true;
		}
		formationState = record.group.GetFormationState();
		if (formationState == eAIGroupFormationState.NONE)
		{
			report.formation_state = "NONE";
		}
		if (formationState == eAIGroupFormationState.IN)
		{
			report.formation_state = "IN";
		}
		if (formationState == eAIGroupFormationState.FLANK)
		{
			report.formation_state = "FLANK";
		}
		report.waypoint_count = record.group.GetWaypoints().Count();
		report.waypoint_index = record.group.m_CurrentWaypointIndex;
		report.in_combat = record.group.IsInCombat();
		if (report.in_combat)
		{
			report.combat_may_override = true;
		}
		while (index < record.members.Count())
		{
			memberRecord = record.members.Get(index);
			memberReport = new MCPAIGroupMember();
			memberReport.object_id = memberRecord.object_id;
			if (memberRecord.member)
			{
				memberReport.alive = memberRecord.member.IsAlive();
				memberReport.speed_limit = memberRecord.member.GetMovementSpeedLimit();
				memberReport.speed_limit_threat = memberRecord.member.GetMovementSpeedLimitUnderThreat();
			}
			report.members.Insert(memberReport);
			index = index + 1;
		}
	}


	// Plan 3.3: everything is validated before any field is applied, and
	// only the fields present are applied. The reply is read back after.
	protected void DispatchAIGroupOrder(MCPCommand command, MCPResult result)
	{
		MCPAIGroupRecord record;
		MCPAIGroup report = new MCPAIGroup();
		bool hasBehaviour = false;
		bool hasFormation = false;
		bool hasWaypoints = false;
		bool hasSpeed = false;
		bool wasHold = false;
		int index = 0;
		int speedValue = 0;
		eAIFormation previousFormation;
		eAIFormation nextFormation;

		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			report.field = "group_id";
			result.ai_group = report;
			return;
		}
		record = FindAIGroupById(command.args.group_id);
		if (!record)
		{
			result.ok = false;
			result.error = "not_found";
			return;
		}
		if (record.group_state == "releasing")
		{
			result.ok = false;
			result.error = "not_found";
			return;
		}
		if (!record.group)
		{
			result.ok = false;
			result.error = "not_found";
			return;
		}
		if (command.args.behaviour != "")
		{
			hasBehaviour = true;
		}
		if (command.args.formation != "")
		{
			hasFormation = true;
		}
		if (command.args.waypoints != null)
		{
			hasWaypoints = true;
		}
		if (command.args.speed_set)
		{
			hasSpeed = true;
		}
		if (!hasBehaviour && !hasFormation && !hasWaypoints && !hasSpeed)
		{
			result.ok = false;
			result.error = "bad_args";
			return;
		}
		if (hasBehaviour && !AIIsBehaviour(command.args.behaviour))
		{
			result.ok = false;
			result.error = "bad_args";
			report.field = "behaviour";
			AIFillBehaviourAllowed(report);
			result.ai_group = report;
			return;
		}
		if (hasFormation && !AIFormationKnown(command.args.formation))
		{
			result.ok = false;
			result.error = "bad_args";
			report.field = "formation";
			AIFillFormationAllowed(report);
			result.ai_group = report;
			return;
		}
		if (hasWaypoints && !AIWaypointsOk(command.args.waypoints, 1))
		{
			result.ok = false;
			result.error = "bad_args";
			report.field = "waypoints";
			result.ai_group = report;
			return;
		}
		if (hasSpeed && command.args.speed != 1 && command.args.speed != 2 && command.args.speed != 3)
		{
			result.ok = false;
			result.error = "bad_args";
			report.field = "speed";
			result.ai_group = report;
			return;
		}
		if (hasSpeed)
		{
			if (command.args.speed == 1)
			{
				speedValue = eAIMovementSpeed.WALK;
			}
			if (command.args.speed == 2)
			{
				speedValue = eAIMovementSpeed.JOG;
			}
			if (command.args.speed == 3)
			{
				speedValue = eAIMovementSpeed.SPRINT;
			}
			AIOrderSaveSpeed(record, speedValue);
		}
		if (hasWaypoints)
		{
			record.waypoints.Clear();
			index = 0;
			while (index < command.args.waypoints.Count())
			{
				record.waypoints.Insert(command.args.waypoints.Get(index));
				index = index + 1;
			}
			AIGroupApplyRoute(record);
		}
		if (hasFormation)
		{
			previousFormation = record.group.GetFormation();
			nextFormation = eAIFormation.Create(command.args.formation);
			if (previousFormation)
			{
				nextFormation.SetScale(previousFormation.GetScale());
				nextFormation.SetLooseness(previousFormation.GetLooseness());
			}
			record.group.SetFormation(nextFormation);
		}
		if (hasBehaviour)
		{
			wasHold = false;
			if (record.behaviour == "HOLD")
			{
				wasHold = true;
			}
			record.behaviour = command.args.behaviour;
			AIGroupApplyMode(record);
			if (!wasHold || record.behaviour != "HOLD")
			{
				AIGroupApplyLimits(record);
			}
		}
		else if (hasSpeed && record.behaviour != "HOLD")
		{
			AIGroupApplyLimits(record);
		}
		AIFillGroupOrderFields(record, report);
		result.ai_group = report;
		result.ok = true;
	}

	// True only while Expansion still returns this exact group for its id
	// (plan 3.4/3.6): a reused id naming another group is not this one, and
	// holding the reference alone does not prove it is still registered.
	protected bool AIGroupOriginalPresent(MCPAIGroupRecord record)
	{
		eAIGroup found;

		if (!record)
		{
			return false;
		}

		if (!record.group)
		{
			return false;
		}

		if (record.expansion_id <= 0)
		{
			return false;
		}

		found = eAIGroup.GetGroupByID(record.expansion_id, false);
		if (found == record.group)
		{
			return true;
		}

		return false;
	}

	// One member read (plan 3.4). Only getters and public fields are read:
	// no setter and none of the target reads that would update state while
	// reading. A live read refreshes the last observation; a gone member
	// answers with it, marked as such.
	protected void FillAIMemberState(MCPAIGroupMemberRecord memberRecord, MCPAIGroupMember row)
	{
		eAIFSM fsm;
		ExpansionState fsmState;
		ExpansionFSM subFsm;
		ExpansionState subState;
		EmoteManager emoteManager;
		vector memberPos;

		row.object_id = memberRecord.object_id;
		if (!memberRecord.member)
		{
			row.gone = true;
			row.last_observation = true;
			row.alive = memberRecord.last_alive;
			row.health = memberRecord.last_health;
			if (memberRecord.last_pos.Count() > 2)
			{
				row.pos.Insert(memberRecord.last_pos.Get(0));
				row.pos.Insert(memberRecord.last_pos.Get(1));
				row.pos.Insert(memberRecord.last_pos.Get(2));
			}

			return;
		}

		row.alive = memberRecord.member.IsAlive();
		row.health = memberRecord.member.GetHealth();
		memberPos = memberRecord.member.GetPosition();
		VectorToArray(memberPos, row.pos);
		VectorToArray(memberPos, memberRecord.last_pos);
		memberRecord.last_health = row.health;
		memberRecord.last_alive = row.alive;
		fsm = memberRecord.member.GetFSM();
		if (fsm)
		{
			fsmState = fsm.GetState();
			if (fsmState)
			{
				row.fsm_state = fsmState.GetName();
				subFsm = fsmState.GetSubFSM();
				if (subFsm)
				{
					subState = subFsm.GetState();
					if (subState)
					{
						row.fsm_substate = subState.GetName();
					}
				}
			}
		}

		row.threat = memberRecord.member.GetThreatToSelf(true);
		row.fighting = memberRecord.member.m_eAI_IsFightingFSM;
		row.speed_limit = memberRecord.member.GetMovementSpeedLimit();
		row.speed_limit_threat = memberRecord.member.GetMovementSpeedLimitUnderThreat();
		emoteManager = memberRecord.member.GetEmoteManager();
		if (emoteManager)
		{
			row.emote_playing = emoteManager.m_bEmoteIsPlaying;
			row.emote_pending = emoteManager.m_bEmoteIsRequestPending;
		}

		if (memberRecord.member.m_Expansion_EmoteID != 0)
		{
			row.emote_pending = true;
		}
	}

	// One group read (plan 3.4). Getters of a group Expansion no longer
	// returns are never called: its Expansion-read fields stay unavailable
	// (empty strings, waypoint_index -1, no current waypoint) while its
	// registered members are still read and behaviour answers from the
	// registry. Behaviour is read back through AIFillBehaviour, the helper
	// the spawn and order replies share, so the three verbs answer alike
	// for the same group; ROAMING_LOCAL refines ROAMING when Expansion
	// roams locally (SetWaypointBehaviour stores it as ROAMING).
	protected MCPAIGroup BuildAIGroupState(MCPAIGroupRecord record)
	{
		MCPAIGroup report = new MCPAIGroup();
		MCPAIGroupMemberRecord memberRecord;
		MCPAIGroupMember row;
		int index = 0;
		int aliveCount = 0;
		int formationStateValue = 0;
		int waypointBehaviourValue = 0;
		bool present = false;

		report.group_id = record.group_id;
		report.scene_id = record.scene_id;
		report.behaviour = record.behaviour;
		report.members_total = record.members.Count();
		report.waypoint_index = -1;
		present = AIGroupOriginalPresent(record);
		report.group_present = present;
		if (present)
		{
			if (record.group.GetFaction())
			{
				report.faction = record.group.GetFaction().GetName();
			}

			if (record.group.GetFormation())
			{
				report.formation = record.group.GetFormation().GetName();
			}

			formationStateValue = record.group.GetFormationState();
			if (formationStateValue == eAIGroupFormationState.NONE)
			{
				report.formation_state = "NONE";
			}

			if (formationStateValue == eAIGroupFormationState.IN)
			{
				report.formation_state = "IN";
			}

			if (formationStateValue == eAIGroupFormationState.FLANK)
			{
				report.formation_state = "FLANK";
			}

			waypointBehaviourValue = record.group.GetWaypointBehaviour();
			AIFillBehaviour(record.group, report);
			if (waypointBehaviourValue == eAIWaypointBehavior.ROAMING && record.group.m_RoamingLocal)
			{
				report.behaviour_expansion = "ROAMING_LOCAL";
			}

			report.waypoint_index = record.group.m_CurrentWaypointIndex;
			report.waypoint_count = record.group.GetWaypoints().Count();
			VectorToArray(record.group.GetCurrentWaypoint(), report.current_waypoint);
			report.in_combat = record.group.IsInCombat();
		}

		while (index < record.members.Count())
		{
			memberRecord = record.members.Get(index);
			row = new MCPAIGroupMember();
			FillAIMemberState(memberRecord, row);
			if (!row.gone && row.alive)
			{
				aliveCount = aliveCount + 1;
			}

			report.members.Insert(row);
			index = index + 1;
		}

		report.alive = aliveCount;
		// Callers already answer not_found for a releasing group that is gone.
		if (record.group_state == "releasing")
		{
			report.release = "pending";
			report.confirmed = false;
			FillRemainingIds(record, report);
		}

		return report;
	}

	// ai_group_state (plan 3.4): read-only, answered in this dispatch with
	// no job and no setter. Exactly one of group_id and scene_id.
	protected void DispatchAIGroupState(MCPCommand command, MCPResult result)
	{
		MCPAIGroupRecord record;
		MCPAIGroup report;
		int index = 0;
		bool hasGroup = false;
		bool hasScene = false;

		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return;
		}

		if (command.args.group_id > 0)
		{
			hasGroup = true;
		}

		if (command.args.scene_id != "")
		{
			hasScene = true;
		}

		if (hasGroup && hasScene)
		{
			result.ok = false;
			result.error = "bad_args";
			return;
		}

		if (!hasGroup && !hasScene)
		{
			result.ok = false;
			result.error = "bad_args";
			return;
		}

		EnsureAIRegistry();
		if (hasGroup)
		{
			record = FindAIGroupById(command.args.group_id);
			if (!record)
			{
				result.ok = false;
				result.error = "not_found";
				return;
			}

			if (record.group_state == "releasing" && AIGroupGone(record))
			{
				result.ok = false;
				result.error = "not_found";
				return;
			}

			report = BuildAIGroupState(record);
			result.ai_group = report;
			result.ok = true;
			return;
		}

		if (!IsValidSceneId(command.args.scene_id))
		{
			result.ok = false;
			result.error = "bad_args";
			return;
		}

		result.ai_groups = new array<ref MCPAIGroup>();
		while (index < m_AIGroups.Count())
		{
			record = m_AIGroups.Get(index);
			if (record.scene_id == command.args.scene_id)
			{
				if (record.group_state != "releasing" || !AIGroupGone(record))
				{
					report = BuildAIGroupState(record);
					result.ai_groups.Insert(report);
				}
			}

			index = index + 1;
		}

		result.ok = true;
	}

	// ai_emote's closed catalog: the EmoteConstants names of actor_emote
	// (MCPClientBridge.c ActorEmoteId), lowercase. 0 for any other name.
	protected int AIEmoteId(string emote)
	{
		if (emote == "greeting")
		{
			return EmoteConstants.ID_EMOTE_GREETING;
		}
		if (emote == "sos")
		{
			return EmoteConstants.ID_EMOTE_SOS;
		}
		if (emote == "heart")
		{
			return EmoteConstants.ID_EMOTE_HEART;
		}
		if (emote == "taunt")
		{
			return EmoteConstants.ID_EMOTE_TAUNT;
		}
		if (emote == "lyingdown")
		{
			return EmoteConstants.ID_EMOTE_LYINGDOWN;
		}
		if (emote == "tauntkiss")
		{
			return EmoteConstants.ID_EMOTE_TAUNTKISS;
		}
		if (emote == "facepalm")
		{
			return EmoteConstants.ID_EMOTE_FACEPALM;
		}
		if (emote == "tauntelbow")
		{
			return EmoteConstants.ID_EMOTE_TAUNTELBOW;
		}
		if (emote == "thumb")
		{
			return EmoteConstants.ID_EMOTE_THUMB;
		}
		if (emote == "throat")
		{
			return EmoteConstants.ID_EMOTE_THROAT;
		}
		if (emote == "dance")
		{
			return EmoteConstants.ID_EMOTE_DANCE;
		}
		if (emote == "campfire")
		{
			return EmoteConstants.ID_EMOTE_CAMPFIRE;
		}
		if (emote == "sita")
		{
			return EmoteConstants.ID_EMOTE_SITA;
		}
		if (emote == "sitb")
		{
			return EmoteConstants.ID_EMOTE_SITB;
		}
		if (emote == "thumbdown")
		{
			return EmoteConstants.ID_EMOTE_THUMBDOWN;
		}
		if (emote == "dabbing")
		{
			return EmoteConstants.ID_EMOTE_DABBING;
		}
		if (emote == "timeout")
		{
			return EmoteConstants.ID_EMOTE_TIMEOUT;
		}
		if (emote == "clap")
		{
			return EmoteConstants.ID_EMOTE_CLAP;
		}
		if (emote == "point")
		{
			return EmoteConstants.ID_EMOTE_POINT;
		}
		if (emote == "silent")
		{
			return EmoteConstants.ID_EMOTE_SILENT;
		}
		if (emote == "salute")
		{
			return EmoteConstants.ID_EMOTE_SALUTE;
		}
		if (emote == "rps")
		{
			return EmoteConstants.ID_EMOTE_RPS;
		}
		if (emote == "watching")
		{
			return EmoteConstants.ID_EMOTE_WATCHING;
		}
		if (emote == "hold")
		{
			return EmoteConstants.ID_EMOTE_HOLD;
		}
		if (emote == "listening")
		{
			return EmoteConstants.ID_EMOTE_LISTENING;
		}
		if (emote == "pointself")
		{
			return EmoteConstants.ID_EMOTE_POINTSELF;
		}
		if (emote == "lookatme")
		{
			return EmoteConstants.ID_EMOTE_LOOKATME;
		}
		if (emote == "tauntthink")
		{
			return EmoteConstants.ID_EMOTE_TAUNTTHINK;
		}
		if (emote == "move")
		{
			return EmoteConstants.ID_EMOTE_MOVE;
		}
		if (emote == "down")
		{
			return EmoteConstants.ID_EMOTE_DOWN;
		}
		if (emote == "come")
		{
			return EmoteConstants.ID_EMOTE_COME;
		}
		if (emote == "rps_r")
		{
			return EmoteConstants.ID_EMOTE_RPS_R;
		}
		if (emote == "rps_p")
		{
			return EmoteConstants.ID_EMOTE_RPS_P;
		}
		if (emote == "rps_s")
		{
			return EmoteConstants.ID_EMOTE_RPS_S;
		}
		if (emote == "nod")
		{
			return EmoteConstants.ID_EMOTE_NOD;
		}
		if (emote == "shake")
		{
			return EmoteConstants.ID_EMOTE_SHAKE;
		}
		if (emote == "shrug")
		{
			return EmoteConstants.ID_EMOTE_SHRUG;
		}
		return 0;
	}

	protected MCPAIGroupRecord AIFindEmoteGroup(int objectId)
	{
		int groupIndex = 0;
		int memberIndex = 0;
		MCPAIGroupRecord record;
		MCPAIGroupMemberRecord memberRecord;

		EnsureAIRegistry();
		while (groupIndex < m_AIGroups.Count())
		{
			record = m_AIGroups.Get(groupIndex);
			if (record.group_state == "active")
			{
				memberIndex = 0;
				while (memberIndex < record.members.Count())
				{
					memberRecord = record.members.Get(memberIndex);
					if (memberRecord.object_id == objectId && memberRecord.member && memberRecord.member.IsAlive())
					{
						return record;
					}

					memberIndex = memberIndex + 1;
				}
			}

			groupIndex = groupIndex + 1;
		}

		return null;
	}

	protected MCPAIGroupMemberRecord AIFindEmoteMember(MCPAIGroupRecord record, int objectId)
	{
		int memberIndex = 0;
		MCPAIGroupMemberRecord memberRecord;

		if (!record)
		{
			return null;
		}

		while (memberIndex < record.members.Count())
		{
			memberRecord = record.members.Get(memberIndex);
			if (memberRecord.object_id == objectId)
			{
				return memberRecord;
			}

			memberIndex = memberIndex + 1;
		}

		return null;
	}

	protected bool AIEmoteJobOpen(int objectId)
	{
		int index = 0;
		MCPJob openJob;

		if (!m_Jobs)
		{
			return false;
		}

		while (index < m_Jobs.Count())
		{
			openJob = m_Jobs.GetElement(index);
			if (openJob && openJob.kind == "ai_emote" && openJob.emote_member_id == objectId)
			{
				return true;
			}

			index = index + 1;
		}

		return false;
	}

	protected bool AIEmotePending(eAIBase member, EmoteManager emotes)
	{
		if (member && member.m_Expansion_EmoteID != 0)
		{
			return true;
		}

		if (emotes && emotes.m_bEmoteIsRequestPending)
		{
			return true;
		}

		return false;
	}

	protected bool AIEmotePendingByObjectId(int objectId)
	{
		int groupIndex = 0;
		int memberIndex = 0;
		MCPAIGroupRecord record;
		MCPAIGroupMemberRecord memberRecord;

		if (objectId <= 0)
		{
			return false;
		}

		EnsureAIRegistry();
		while (groupIndex < m_AIGroups.Count())
		{
			record = m_AIGroups.Get(groupIndex);
			if (record.group_state == "active" || record.group_state == "releasing")
			{
				memberIndex = 0;
				while (memberIndex < record.members.Count())
				{
					memberRecord = record.members.Get(memberIndex);
					if (memberRecord.object_id == objectId && memberRecord.member)
					{
						return AIEmotePending(memberRecord.member, memberRecord.member.GetEmoteManager());
					}

					memberIndex = memberIndex + 1;
				}
			}

			groupIndex = groupIndex + 1;
		}

		return false;
	}

	// Static poses schedule m_EmoteManager.ServerRequestEmoteCancel on the
	// system call queue (eAIBase.c:10914; 4_world/classes/emotemanager.c:1275).
	// Remove that bound call (2_gamelib/tools.c:65) before ending or starting
	// an emote so an earlier pose's expiry cannot cancel the next one.
	protected void AIClearEmoteAutoCancel(EmoteManager emotes)
	{
		ScriptCallQueue queue;

		if (!emotes || !GetGame())
		{
			return;
		}

		queue = GetGame().GetCallQueue(CALL_CATEGORY_SYSTEM);
		if (queue)
		{
			queue.Remove(emotes.ServerRequestEmoteCancel);
		}
	}

	// ai_emote (plan 3.5): the dispatch, in its order, is the only place
	// this verb writes. Its job (IsJobReady below) only reads.
	protected int DispatchAIEmote(MCPCommand command, MCPResult result)
	{
		MCPAIGroupRecord record;
		MCPAIGroupMemberRecord memberRecord;
		eAIBase member;
		EmoteManager emotes;
		MCPJob job;
		int emoteId = 0;
		bool isEnd = false;

		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			result.emote_pending = false;
			return 1;
		}

		isEnd = command.args.emote == "end";
		if (!isEnd)
		{
			emoteId = AIEmoteId(command.args.emote);
			if (emoteId <= 0)
			{
				result.ok = false;
				result.error = "bad_args";
				result.emote_pending = AIEmotePendingByObjectId(command.args.object_id);
				return 1;
			}
		}

		if (!IsFiniteFloat(command.args.wait_s) || command.args.wait_s < AI_EMOTE_WAIT_MIN_S || command.args.wait_s > AI_EMOTE_WAIT_MAX_S)
		{
			result.ok = false;
			result.error = "bad_args";
			result.emote_pending = AIEmotePendingByObjectId(command.args.object_id);
			return 1;
		}

		// 1. A living member of an active group.
		record = AIFindEmoteGroup(command.args.object_id);
		if (!record)
		{
			result.ok = false;
			result.error = "bad_target";
			result.emote_pending = AIEmotePendingByObjectId(command.args.object_id);
			return 1;
		}

		memberRecord = AIFindEmoteMember(record, command.args.object_id);
		member = memberRecord.member;
		emotes = member.GetEmoteManager();
		if (!emotes)
		{
			result.ok = false;
			result.error = "emote_rejected";
			result.emote_state = "rejected";
			result.emote_pending = AIEmotePending(member, emotes);
			return 1;
		}

		// 2. One emote job per member, end included.
		if (AIEmoteJobOpen(command.args.object_id))
		{
			result.ok = false;
			result.error = "emote_rejected";
			result.emote_state = "rejected";
			result.emote_pending = AIEmotePending(member, emotes);
			return 1;
		}

		if (isEnd)
		{
			if (emotes.m_bEmoteIsPlaying)
			{
				if (member.m_Expansion_EmoteID != 0)
				{
					member.Expansion_SetEmote(0);
				}

				AIClearEmoteAutoCancel(emotes);
				emotes.ServerRequestEmoteCancel();
				job = new MCPJob();
				job.id = command.id;
				job.kind = "ai_emote";
				job.args = command.args;
				job.subject = member;
				job.emote_end = true;
				job.emote_member_id = command.args.object_id;
				job.emote_request_s = GetGame().GetTickTime();
				job.wait_s = command.args.wait_s;
				job.deadline_s = m_ElapsedS + command.args.wait_s;
				job.tick_poll_sent = result.tick_poll_sent;
				job.tick_poll_callback = result.tick_poll_callback;
				job.tick_dispatch = result.tick_dispatch;
				m_Jobs.Insert(job.id, job);
				return 2;
			}

			if (member.m_Expansion_EmoteID != 0)
			{
				member.Expansion_SetEmote(0);
				result.ok = true;
				result.emote_state = "cancelled";
				result.emote_pending = AIEmotePending(member, emotes);
				return 1;
			}

			if (emotes.m_bEmoteIsRequestPending)
			{
				result.ok = false;
				result.error = "emote_rejected";
				result.emote_state = "rejected";
				result.emote_pending = true;
				return 1;
			}

			result.ok = false;
			result.error = "no_emote_playing";
			result.emote_state = "rejected";
			result.emote_pending = AIEmotePending(member, emotes);
			return 1;
		}

		// 3. Nothing sounding and no request in either layer.
		if (member.m_Expansion_EmoteID != 0 || emotes.IsEmotePlaying())
		{
			result.ok = false;
			result.error = "emote_rejected";
			result.emote_state = "rejected";
			result.emote_pending = AIEmotePending(member, emotes);
			return 1;
		}

		// 4. The PlayEmote guard (eAIState_PlayEmote.c:13, :27), read first.
		if (member.m_eAI_IsFightingFSM || (record.group && record.group.IsInCombat()) || member.GetThreatToSelf(true) > 0.2)
		{
			result.ok = false;
			result.error = "emote_rejected";
			result.emote_state = "rejected";
			result.emote_pending = AIEmotePending(member, emotes);
			return 1;
		}

		// 5. The one write: autoCancel consumes it in Expansion's attempt.
		AIClearEmoteAutoCancel(emotes);
		member.Expansion_SetEmote(emoteId, true, Math.Floor(command.args.wait_s * 1000.0));
		job = new MCPJob();
		job.id = command.id;
		job.kind = "ai_emote";
		job.args = command.args;
		job.subject = member;
		job.emote_end = false;
		job.emote_gesture_id = emoteId;
		job.emote_member_id = command.args.object_id;
		job.emote_request_s = GetGame().GetTickTime();
		job.wait_s = command.args.wait_s;
		job.deadline_s = m_ElapsedS + command.args.wait_s;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_Jobs.Insert(job.id, job);
		return 2;
	}

	// The ai_emote job only reads the member's EmoteManager and Expansion
	// request, and writes only its own fields (plan 3.5).
	protected override bool IsJobReady(MCPJob job)
	{
		eAIBase member;
		EmoteManager emotes;
		float nowS = 0.0;
		bool playing = false;

		if (job.kind != "ai_emote")
		{
			return super.IsJobReady(job);
		}

		nowS = GetGame().GetTickTime();
		member = eAIBase.Cast(job.subject);
		if (!member || !member.IsAlive())
		{
			job.emote_interrupted = true;
			job.emote_pending = false;
			if (member)
			{
				job.emote_pending = AIEmotePending(member, member.GetEmoteManager());
			}

			return true;
		}

		if (nowS - job.emote_request_s > job.wait_s)
		{
			return false;
		}

		emotes = member.GetEmoteManager();
		job.emote_pending = AIEmotePending(member, emotes);
		if (emotes)
		{
			playing = emotes.m_bEmoteIsPlaying;
		}

		if (job.emote_end)
		{
			if (playing)
			{
				return false;
			}

			job.emote_finished_s = nowS;
			return true;
		}

		if (playing)
		{
			if (!job.emote_started && emotes && emotes.Expansion_GetCurrentGesture() == job.emote_gesture_id)
			{
				job.emote_started = true;
				job.emote_started_s = nowS;
			}

			return false;
		}

		if (!job.emote_started)
		{
			return false;
		}

		job.emote_finished_s = nowS;
		return true;
	}

	protected override void PostJobSuccess(MCPJob job)
	{
		MCPResult result;

		if (job.kind != "ai_emote")
		{
			super.PostJobSuccess(job);
			return;
		}

		result = new MCPResult();
		result.id = job.id;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		result.emote_pending = job.emote_pending;
		if (job.emote_started && !job.emote_end)
		{
			result.started_after_s = job.emote_started_s - job.emote_request_s;
		}

		if (job.emote_interrupted)
		{
			result.ok = false;
			result.error = "emote_interrupted";
			result.emote_state = "interrupted";
			PostResult(result);
			return;
		}

		result.ok = true;
		result.emote_state = "finished";
		result.finished_after_s = job.emote_finished_s - job.emote_request_s;
		PostResult(result);
	}

	protected override void PostJobTimeout(MCPJob job)
	{
		MCPResult result;
		eAIBase member;

		if (job.kind != "ai_emote")
		{
			super.PostJobTimeout(job);
			return;
		}

		result = new MCPResult();
		result.id = job.id;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		// Read here: this tick is past the last IsJobReady read, and the
		// lease close calls this without any tick (MCPBridge.c:8661).
		member = eAIBase.Cast(job.subject);
		if (member)
		{
			result.emote_pending = AIEmotePending(member, member.GetEmoteManager());
		}
		if (job.emote_started && !job.emote_end)
		{
			result.ok = true;
			result.emote_state = "started";
			result.started_after_s = job.emote_started_s - job.emote_request_s;
		}
		else
		{
			result.ok = false;
			result.error = "emote_timed_out";
			result.emote_state = "timed_out";
		}

		PostResult(result);
	}

	protected void DispatchAIGroupRelease(MCPCommand command, MCPResult result)
	{
		MCPAIGroupRecord record;
		MCPAIGroup report = new MCPAIGroup();
		bool gone = false;

		result.ai_group = report;
		if (!command.args || command.args.spawn_token == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return;
		}

		record = FindAIGroupByToken(command.args.spawn_token);
		if (!record)
		{
			// An abandoned spawn may never have created anything.
			report.released = 0;
			result.ok = true;
			return;
		}

		gone = AIGroupRequestRelease(record);
		if (gone)
		{
			report.release = "released";
			report.confirmed = true;
		}
		else
		{
			report.release = "pending";
			report.confirmed = false;
			FillRemainingIds(record, report);
		}

		result.ok = true;
	}
};
