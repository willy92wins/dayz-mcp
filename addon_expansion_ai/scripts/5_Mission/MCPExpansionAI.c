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
	eAIGroup group;
	int expansion_id;
	string scene_id;
	string behaviour;
	string group_state;
	ref array<ref MCPAIGroupMemberRecord> members;

	void MCPAIGroupRecord()
	{
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

	protected override string ServerCapabilities()
	{
		return super.ServerCapabilities() + ",ai_group_release";
	}

	protected override string ExtensionPollParams()
	{
		return "&xai=" + EXPANSION_AI_ARG_CONTRACT_HASH;
	}

	protected override int DispatchExtension(MCPCommand command, MCPResult result)
	{
		if (command.cmd == "ai_group_release")
		{
			DispatchAIGroupRelease(command, result);
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
