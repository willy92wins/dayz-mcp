// infected_drive (inbox 7c86): hold an infected's heading and speed by
// re-applying OverrideTurnSpeed, OverrideMovementSpeed and OverrideHeading on
// its server DayZInfectedInputController on every bridge tick (Tick, called
// from MCPBridge.OnTick). One call alone stops after a few seconds once the
// infected AI overwrites the controller; vanilla command handlers re-apply the
// three overrides every frame (plugindayzinfecteddebug.c:380-385;
// dayzanimal.c:548-550). One entry per driven DayZInfected, holding the plain
// DayZInfected reference (nulled by the engine once deleted, enentity.c:164),
// the command id that set or last replaced the drive, the heading in radians
// and the speed. A new drive on an already driven infected replaces its
// heading, speed and command id. An entry ends on mode="release" (Release,
// while MCPBridge.DispatchInfectedDrive disables the three overrides with or
// without an entry), on the infected's death or deletion (Tick, with the
// three set to false while the entity still exists), at bridge shutdown
// (ShutdownAll), at the lease close (DispatchLeaseClose calls ShutdownAll)
// and on infected_drive_release (ReleaseCommand), which the broker sends
// with command_id only when a delivered drive is abandoned mid-lease.
class MCPInfectedDriveEntry
{
	DayZInfected infected;
	// The infected_drive command that set the drive: the first one, or the
	// latest that replaced it. infected_drive_release names it.
	int command_id;
	float heading_rad;
	float speed;
};

class MCPInfectedDrive
{
	protected static ref array<ref MCPInfectedDriveEntry> s_Driven;

	// Starts the drive, or replaces the heading, speed and command id of an
	// already driven infected, and applies the three overrides at once.
	static void Drive(DayZInfected infected, float headingRad, float speed, int commandId)
	{
		int index;
		MCPInfectedDriveEntry entry;
		if (!infected)
		{
			return;
		}
		if (!s_Driven)
		{
			s_Driven = new array<ref MCPInfectedDriveEntry>();
		}
		index = Find(infected);
		if (index >= 0)
		{
			entry = s_Driven.Get(index);
		}
		else
		{
			entry = new MCPInfectedDriveEntry();
			entry.infected = infected;
			s_Driven.Insert(entry);
		}
		entry.command_id = commandId;
		entry.heading_rad = headingRad;
		entry.speed = speed;
		Apply(infected, entry);
	}

	// Removes infected's entry if any.
	// MCPBridge.DispatchInfectedDrive sets the three overrides to false whether
	// or not an entry existed.
	static void Release(DayZInfected infected)
	{
		int index;
		if (!infected)
		{
			return;
		}
		index = Find(infected);
		if (index < 0)
		{
			return;
		}
		s_Driven.Remove(index);
	}

	// infected_drive_release: removes, with the three overrides set to false,
	// every entry that commandId set, and answers whether one was removed. Any
	// other command id changes nothing. Command ids restart with each daemon,
	// so an entry a previous daemon set with the same id, which no lease owns
	// any more, goes too.
	static bool ReleaseCommand(int commandId)
	{
		int index;
		bool matched;
		MCPInfectedDriveEntry entry;
		if (commandId <= 0 || !s_Driven)
		{
			return false;
		}
		matched = false;
		index = s_Driven.Count() - 1;
		while (index >= 0)
		{
			entry = s_Driven.Get(index);
			if (entry && entry.command_id == commandId)
			{
				s_Driven.Remove(index);
				Disable(entry.infected);
				matched = true;
			}
			index = index - 1;
		}
		return matched;
	}

	// Every server bridge tick. Backwards, so the removal of one entry (the
	// last one moves into its place) skips none.
	static void Tick()
	{
		int index;
		MCPInfectedDriveEntry entry;
		DayZInfected infected;
		if (!s_Driven)
		{
			return;
		}
		index = s_Driven.Count() - 1;
		while (index >= 0)
		{
			entry = s_Driven.Get(index);
			infected = null;
			if (entry)
			{
				infected = entry.infected;
			}
			if (!entry || !infected || !infected.IsAlive())
			{
				s_Driven.Remove(index);
				Disable(infected);
			}
			else
			{
				Apply(infected, entry);
			}
			index = index - 1;
		}
	}

	static void ShutdownAll()
	{
		int index;
		MCPInfectedDriveEntry entry;
		if (!s_Driven)
		{
			return;
		}
		index = s_Driven.Count() - 1;
		while (index >= 0)
		{
			entry = s_Driven.Get(index);
			if (entry)
			{
				Disable(entry.infected);
			}
			index = index - 1;
		}
		s_Driven.Clear();
	}

	protected static void Apply(DayZInfected infected, MCPInfectedDriveEntry entry)
	{
		DayZInfectedInputController controller;
		if (!infected || !entry)
		{
			return;
		}
		controller = infected.GetInputController();
		if (!controller)
		{
			return;
		}
		controller.OverrideTurnSpeed(true, Math.PI2);
		controller.OverrideMovementSpeed(true, entry.speed);
		controller.OverrideHeading(true, entry.heading_rad);
	}

	protected static void Disable(DayZInfected infected)
	{
		DayZInfectedInputController controller;
		if (!infected)
		{
			return;
		}
		controller = infected.GetInputController();
		if (!controller)
		{
			return;
		}
		controller.OverrideMovementSpeed(false, 0.0);
		controller.OverrideHeading(false, 0.0);
		controller.OverrideTurnSpeed(false, 0.0);
	}

	protected static int Find(DayZInfected infected)
	{
		int index;
		MCPInfectedDriveEntry entry;
		if (!infected || !s_Driven)
		{
			return -1;
		}
		index = 0;
		while (index < s_Driven.Count())
		{
			entry = s_Driven.Get(index);
			if (entry && entry.infected == infected)
			{
				return index;
			}
			index = index + 1;
		}
		return -1;
	}
};
