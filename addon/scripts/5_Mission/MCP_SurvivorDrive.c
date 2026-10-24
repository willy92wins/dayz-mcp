// survivor_drive (inbox c801): walk a survivor this bridge registered
// (world_spawn's object_id) with no client and no OS input, through the input
// overrides alone: OverrideMovementSpeed and OverrideMovementAngle ENABLED on
// the survivor's server HumanInputController (human.c:253-256; ENABLED holds
// until DISABLED, :7-12), passed as MCPPlayerMoveControl.Enable passes them.
// The angle is the wanted compass heading minus the compass heading of
// GetHeadingAngle, wrapped to -180..180, recomputed on every server bridge
// tick (Tick, the MCPBotControl.Tick pattern). A driven id is dropped, with
// DISABLED, on release, on the survivor's death or deletion (Tick,
// OnObjectGone) and at bridge shutdown (ShutdownAll). A new drive on a driven
// id replaces its heading and speed. Nothing else ends a drive: the survivor
// keeps walking until one of those. MCPBridge.DispatchSurvivorDrive checks its
// arguments (IsDriveSpeed) and the target before it calls Drive or Release.
class MCPSurvivorDriveEntry
{
	int object_id;
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

	// Starts the drive, or replaces the heading and speed of a driven id, and
	// applies it at once.
	static void Drive(PlayerBase survivor, int objectId, float headingDeg, float speed)
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

	// object_delete, before the object goes.
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
