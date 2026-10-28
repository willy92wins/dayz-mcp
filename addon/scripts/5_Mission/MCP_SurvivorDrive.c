// survivor_drive (inbox c801): walk a survivor this bridge registered
// (world_spawn's object_id) with no client and no OS input, through the input
// overrides alone: OverrideMovementSpeed and OverrideMovementAngle ENABLED on
// the survivor's server HumanInputController (1.30 human.c:253-256; ENABLED
// holds until DISABLED, 1.29 and 1.30 human.c:7-12), passed as
// MCPPlayerMoveControl.Enable passes them. The angle is the wanted compass
// heading minus the compass heading of GetHeadingAngle, wrapped to -180..180,
// recomputed on every server bridge tick (Tick, the MCPBotControl.Tick
// pattern). A driven id is dropped, with DISABLED, on release, on the
// survivor's death or deletion (Tick, OnObjectGone), at bridge shutdown
// (ShutdownAll) and on survivor_drive_release (ReleaseCommand), which the
// broker sends with the id of a drive command and its survivor's object_id when
// the lease that sent it ends or abandons it: that drops that survivor's drive
// only while the command's own drive is the one set. A new drive on a driven id
// replaces its heading, speed and command id. Nothing else ends a drive: the
// survivor keeps walking until one of those. MCPBridge.DispatchSurvivorDrive
// checks its arguments (IsDriveSpeed) and the target before it calls Drive or
// Release, and MCPBridge.DispatchBotStart refuses a driven id (IsDriven,
// drive_busy).
class MCPSurvivorDriveEntry
{
	int object_id;
	// The survivor_drive command that set the drive: the first one, or the
	// latest that replaced it. survivor_drive_release names it, with object_id.
	int command_id;
	float heading_deg;
	float speed;
};

class MCPSurvivorDrive
{
	protected static ref array<ref MCPSurvivorDriveEntry> s_Driven;

	// walk, jog or sprint: the OverrideMovementSpeed values player_move maps
	// (1, 2, 3). The server bridge asks here, so MCPBridge.c names no
	// player_move code.
	static bool IsDriveSpeed(float speed)
	{
		return MCPPlayerMoveControl.SpeedName(speed) != "";
	}

	// True while the id is driven. MCPBridge.DispatchBotStart refuses such an id
	// with drive_busy, as DispatchSurvivorDrive refuses one a bot runs on with
	// bot_busy: Tick would keep re-applying the overrides under the bot.
	static bool IsDriven(int objectId)
	{
		return Find(objectId) >= 0;
	}

	// Starts the drive, or replaces the heading, speed and command id of a
	// driven id, and applies it at once. commandId is the id of the
	// survivor_drive command, the one survivor_drive_release names with
	// objectId.
	static void Drive(PlayerBase survivor, int objectId, float headingDeg, float speed, int commandId)
	{
		int index;
		MCPSurvivorDriveEntry entry;
		if (!s_Driven)
		{
			s_Driven = new array<ref MCPSurvivorDriveEntry>();
		}
		index = Find(objectId);
		if (index >= 0)
		{
			entry = s_Driven.Get(index);
		}
		else
		{
			entry = new MCPSurvivorDriveEntry();
			entry.object_id = objectId;
			s_Driven.Insert(entry);
		}
		entry.command_id = commandId;
		entry.heading_deg = headingDeg;
		entry.speed = speed;
		Apply(survivor, entry);
	}

	// Drops the id with DISABLED. An id that is not driven changes nothing:
	// DISABLED is applied only where this code enabled the overrides.
	static void Release(MCPBridge bridge, int objectId)
	{
		int index;
		Object target;
		index = Find(objectId);
		if (index < 0)
		{
			return;
		}
		s_Driven.Remove(index);
		target = null;
		if (bridge)
		{
			target = bridge.RuntimeObjectById(objectId);
		}
		Disable(PlayerBase.Cast(target));
	}

	// survivor_drive_release: drops objectId's drive, with DISABLED as Release
	// does, when commandId set it, and answers true. Anything else changes
	// nothing and answers false: an id that is not driven, or a drive another
	// command set, such as a later one that replaced it. The release names the
	// survivor because command ids restart with each daemon, so the same id
	// can have set another survivor's drive.
	static bool ReleaseCommand(MCPBridge bridge, int objectId, int commandId)
	{
		int index;
		MCPSurvivorDriveEntry entry;
		index = Find(objectId);
		if (index < 0)
		{
			return false;
		}
		entry = s_Driven.Get(index);
		if (!entry || entry.command_id != commandId)
		{
			return false;
		}
		Release(bridge, objectId);
		return true;
	}

	// object_delete and scene_clear, before the object goes.
	static void OnObjectGone(MCPBridge bridge, int objectId)
	{
		Release(bridge, objectId);
	}

	// Every server bridge tick. Backwards, so the removal of one entry (the
	// last one moves into its place) skips none.
	static void Tick(MCPBridge bridge)
	{
		int index;
		MCPSurvivorDriveEntry entry;
		Object target;
		PlayerBase survivor;
		if (!s_Driven)
		{
			return;
		}
		index = s_Driven.Count() - 1;
		while (index >= 0)
		{
			entry = s_Driven.Get(index);
			target = null;
			if (bridge && entry)
			{
				target = bridge.RuntimeObjectById(entry.object_id);
			}
			survivor = PlayerBase.Cast(target);
			if (!entry || !survivor || !survivor.IsAlive())
			{
				s_Driven.Remove(index);
				Disable(survivor);
			}
			else
			{
				Apply(survivor, entry);
			}
			index = index - 1;
		}
	}

	static void ShutdownAll(MCPBridge bridge)
	{
		int index;
		MCPSurvivorDriveEntry entry;
		Object target;
		if (!s_Driven)
		{
			return;
		}
		index = s_Driven.Count() - 1;
		while (index >= 0)
		{
			entry = s_Driven.Get(index);
			target = null;
			if (bridge && entry)
			{
				target = bridge.RuntimeObjectById(entry.object_id);
			}
			Disable(PlayerBase.Cast(target));
			index = index - 1;
		}
		s_Driven.Clear();
	}

	protected static void Apply(PlayerBase survivor, MCPSurvivorDriveEntry entry)
	{
		HumanInputController hic;
		float compass;
		float angle;
		if (!survivor || !entry)
		{
			return;
		}
		hic = survivor.GetInputController();
		if (!hic)
		{
			return;
		}
		compass = MCPPlayerMoveControl.CompassFromHeading(hic.GetHeadingAngle());
		angle = MCPPlayerMoveControl.WrapDeg180(entry.heading_deg - compass);
		MCPPlayerMoveControl.Enable(hic, entry.speed, angle);
	}

	protected static void Disable(PlayerBase survivor)
	{
		HumanInputController hic;
		if (!survivor)
		{
			return;
		}
		hic = survivor.GetInputController();
		if (!hic)
		{
			return;
		}
		MCPPlayerMoveControl.Disable(hic);
	}

	protected static int Find(int objectId)
	{
		int index;
		if (!s_Driven)
		{
			return -1;
		}
		index = 0;
		while (index < s_Driven.Count())
		{
			if (s_Driven.Get(index) && s_Driven.Get(index).object_id == objectId)
			{
				return index;
			}
			index = index + 1;
		}
		return -1;
	}
};
