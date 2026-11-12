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
	ref array<string> allowed;
	ref MCPAIArgsRead args_read;
	string release;
	bool confirmed;
	int released;
	ref array<int> remaining_ids;
	ref array<ref MCPAIGroupMember> members;

	void MCPAIGroup()
	{
		allowed = new array<string>();
		remaining_ids = new array<int>();
		members = new array<ref MCPAIGroupMember>();
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
};

modded class MCPBridge
{
	protected ref array<ref MCPAIGroupRecord> m_AIGroups;

	protected const int AI_GROUP_COUNT_MAX = 8;
	protected const int AI_TOTAL_MAX = 24;
	protected const int AI_WAYPOINTS_MAX = 32;
	protected const float AI_MEMBER_RING_M = 1.5;

	// Must equal EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH in bridge_readiness.py.
	protected const string EXPANSION_AI_ARG_CONTRACT_HASH = "68e2b957803c9a13";

	// Adapter verbs as an xai bitmask (bridge_readiness.EXPANSION_AI_VERB_BITS,
	// append only): bit0 ai_group_release, bit1 ai_group_spawn. The adapter
	// adds no names to caps, so the saturated poll stays within 1024 (#321);
	// the broker expands the mask to names before storing the census.
	protected const string EXPANSION_AI_VERBS = "3";

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
		eAIGroup found;

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

		if (record.group && record.expansion_id > 0)
		{
			found = eAIGroup.GetGroupByID(record.expansion_id, false);
			if (found == record.group)
			{
				return false;
			}
		}

		return true;
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

	protected void AIFillFormationAllowed(MCPAIGroup report)
	{
		int index = 0;
		int total = eAIFormation.s_FormationTypes.Count();
		typename formationType;
		string typeName;

		while (index < total)
		{
			formationType = eAIFormation.s_FormationTypes.Get(index);
			typeName = formationType.ToString();
			report.allowed.Insert(typeName.Substring(12, typeName.Length() - 12));
			index = index + 1;
		}
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

	protected void AIGroupApplyBehaviour(MCPAIGroupRecord record)
	{
		int index = 0;
		int memberIndex = 0;
		MCPAIGroupMemberRecord memberRecord;
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

		if (record.behaviour == "HOLD")
		{
			record.group.SetFormationState(eAIGroupFormationState.NONE);
			record.group.SetWaypointBehaviour(eAIWaypointBehavior.HALT);
		}
		else
		{
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

		while (memberIndex < record.members.Count())
		{
			memberRecord = record.members.Get(memberIndex);
			if (memberRecord.member)
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

			memberIndex = memberIndex + 1;
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
		eAIWaypointBehavior waypointBehaviour;

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

			waypointBehaviour = record.group.GetWaypointBehaviour();
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
		typename formationType;
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
		bool waypointsOk = true;
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

		if (!args.waypoints || args.waypoints.Count() > AI_WAYPOINTS_MAX * 3)
		{
			AISpawnRefuse(result, report, "bad_args", "waypoints");
			return;
		}

		index = 0;
		while (index < args.waypoints.Count() && waypointsOk)
		{
			if (index + 2 >= args.waypoints.Count())
			{
				waypointsOk = false;
			}
			else if (!IsFiniteFloat(args.waypoints.Get(index)) || !IsFiniteFloat(args.waypoints.Get(index + 1)) || !IsFiniteFloat(args.waypoints.Get(index + 2)))
			{
				waypointsOk = false;
			}

			index = index + 3;
		}

		if (!waypointsOk)
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

		// 4. The formation must be known; Create would fall back to Vee, and
		// GetType answers a random formation for "" and RANDOM
		// (eAIFormation.c:125): only the exact class name counts.
		formationType = eAIFormation.GetType(args.formation);
		if (!formationType || formationType.ToString() != "eAIFormation" + args.formation)
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
		AIGroupApplyBehaviour(record);

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
