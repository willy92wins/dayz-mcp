// #298. A per-delete gate; the caller owns the timeout and the actual deletion.
// Vanilla 1.30: scripts/3_game/vehicles/transport.c:178/190 (all crew slots),
// scripts/3_game/human.c:716/722/727-729/1528 (vehicle command and exit).
// Use the normal exit command, not raw CrewGetOut: the driver-left event hides
// the HUD (scripts/4_world/entities/manbase/playerbase.c:4579-4593), whose cached
// CarScript is otherwise dereferenced at scripts/5_mission/gui/vehicles/carhud.c:104.
class MCPVehicleEject
{
	// Survive job timeout/refusal and overlapping guards: native crew membership
	// can clear before the exit command/parent does. A retry must retain that actor
	// and the extra clear tick, including multiple synchronous retries in one frame.
	protected static ref array<Transport> s_PendingVehicles;
	protected static ref array<Human> s_PendingOccupants;
	protected static ref array<int> s_ClearTicks;
	protected ref array<Object> m_Roots;
	protected ref array<Transport> m_Vehicles;
	protected ref array<Human> m_Occupants;
	protected ref array<IEntity> m_Nodes;
	protected ref array<EntityAI> m_Inventory;
	protected ref array<EntityAI> m_InventoryVisited;
	protected ref array<IEntity> m_Siblings;
	protected int m_ClearTick;
	protected bool m_Begun;

	void MCPVehicleEject()
	{
		if (!s_PendingVehicles) { s_PendingVehicles = new array<Transport>(); }
		if (!s_PendingOccupants) { s_PendingOccupants = new array<Human>(); }
		if (!s_ClearTicks) { s_ClearTicks = new array<int>(); }
		m_Roots = new array<Object>();
		m_Vehicles = new array<Transport>();
		m_Occupants = new array<Human>();
		m_Nodes = new array<IEntity>();
		m_Inventory = new array<EntityAI>();
		m_InventoryVisited = new array<EntityAI>();
		m_Siblings = new array<IEntity>();
		m_ClearTick = -1;
		m_Begun = false;
	}

	void Add(Object root)
	{
		if (root && m_Roots.Find(root) < 0)
		{
			m_Roots.Insert(root);
		}
	}

	bool HasVehicles()
	{
		return m_Vehicles.Count() > 0;
	}

	bool HasOccupants()
	{
		return m_Occupants.Count() > 0;
	}

	protected bool IsClear(Transport vehicle, Human occupant)
	{
		if (!vehicle || !occupant) { return true; }
		// A former occupant may already be in another vehicle; only this pair blocks.
		HumanCommandVehicle command = occupant.GetCommand_Vehicle();
		if (command && command.GetTransport() == vehicle) { return false; }
		if (occupant.GetParent() == vehicle) { return false; }
		int seats = vehicle.CrewSize();
		if (seats < 0) { return false; }
		int seat = 0;
		while (seat < seats)
		{
			if (vehicle.CrewMember(seat) == occupant) { return false; }
			seat = seat + 1;
		}
		return true;
	}

	protected void Remember(Transport vehicle, Human occupant)
	{
		int index = 0;
		while (index < s_PendingVehicles.Count())
		{
			if (s_PendingVehicles.Get(index) == vehicle && s_PendingOccupants.Get(index) == occupant)
			{
				s_ClearTicks.Set(index, -1); // array.Set: 1_core/proto/enscript.c:389
				return;
			}
			index = index + 1;
		}
		s_PendingVehicles.Insert(vehicle);
		s_PendingOccupants.Insert(occupant);
		s_ClearTicks.Insert(-1);
	}

	protected void CollectPending(int tick)
	{
		int index = 0;
		while (index < s_PendingVehicles.Count())
		{
			Transport vehicle = s_PendingVehicles.Get(index);
			Human occupant = s_PendingOccupants.Get(index);
			bool completed = false;
			if (!vehicle || !occupant) { completed = true; }
			else if (IsClear(vehicle, occupant))
			{
				int clearTick = s_ClearTicks.Get(index);
				if (clearTick < 0) { s_ClearTicks.Set(index, tick); }
				else if (tick > clearTick) { completed = true; }
			}
			else { s_ClearTicks.Set(index, -1); }
			if (completed)
			{
				// array.Remove swaps the last element (1_core/proto/enscript.c:457-463).
				// Remove the same index in both arrays to preserve each pair.
				s_PendingVehicles.Remove(index);
				s_PendingOccupants.Remove(index);
				s_ClearTicks.Remove(index);
			}
			else
			{
				if (m_Vehicles.Find(vehicle) >= 0 && m_Occupants.Find(occupant) < 0)
				{
					m_Occupants.Insert(occupant);
				}
				index = index + 1;
			}
		}
	}

	protected bool QueueNode(IEntity node)
	{
		if (!node) { return false; }
		if (m_Nodes.Find(node) >= 0) { return true; }
		if (m_Nodes.Count() >= 4096) { return false; }
		m_Nodes.Insert(node);
		return true;
	}

	// Inventory and AddChild/seat hierarchy are separate surfaces (SceneCarrier).
	// Verified: entityai.c:1884 GetInventory; inventory.c:127/134 enumeration/count;
	// gameplay.c:5 PREORDER; 1_core/proto/enentity.c:573/575 GetChildren/GetSibling.
	// EnumerateInventory false can mean empty: compare its count instead of
	// treating every false as an error. Missing/inconsistent traversal fails closed.
	protected bool Collect()
	{
		m_Nodes.Clear();
		m_InventoryVisited.Clear();
		int index = 0;
		while (index < m_Roots.Count())
		{
			Object root = m_Roots.Get(index);
			if (root && !QueueNode(root)) { return false; }
			index = index + 1;
		}
		index = 0;
		while (index < m_Nodes.Count())
		{
			IEntity node = m_Nodes.Get(index);
			Transport vehicle = Transport.Cast(node);
			if (vehicle && m_Vehicles.Find(vehicle) < 0)
			{
				m_Vehicles.Insert(vehicle);
				m_ClearTick = -1;
			}
			EntityAI entity = EntityAI.Cast(node);
			if (entity && m_InventoryVisited.Find(entity) < 0)
			{
				GameInventory inventory = entity.GetInventory();
				if (!inventory) { return false; }
				int count = inventory.CountInventory();
				if (count < 0 || count > 4096) { return false; }
				m_Inventory.Clear();
				bool enumerated = inventory.EnumerateInventory(InventoryTraversalType.PREORDER, m_Inventory);
				if (count > 0 && !enumerated) { return false; }
				if (m_Inventory.Count() != count) { return false; }
				m_InventoryVisited.Insert(entity);
				int itemIndex = 0;
				while (itemIndex < m_Inventory.Count())
				{
					EntityAI item = m_Inventory.Get(itemIndex);
					if (!QueueNode(item)) { return false; }
					if (m_InventoryVisited.Find(item) < 0) { m_InventoryVisited.Insert(item); }
					itemIndex = itemIndex + 1;
				}
			}
			m_Siblings.Clear();
			IEntity child = node.GetChildren();
			while (child)
			{
				if (m_Siblings.Find(child) >= 0) { return false; }
				m_Siblings.Insert(child);
				if (!QueueNode(child)) { return false; }
				child = child.GetSibling();
			}
			index = index + 1;
		}
		return true;
	}

	bool Begin(int tick)
	{
		m_Begun = false;
		m_ClearTick = -1;
		m_Occupants.Clear();
		if (!Collect()) { return false; }
		int vehicleIndex = 0;
		while (vehicleIndex < m_Vehicles.Count())
		{
			Transport vehicle = m_Vehicles.Get(vehicleIndex);
			int seats = vehicle.CrewSize();
			if (seats < 0) { return false; }
			int seat = 0;
			while (seat < seats)
			{
				Human occupant = vehicle.CrewMember(seat);
				if (occupant)
				{
					if (m_Occupants.Find(occupant) < 0) { m_Occupants.Insert(occupant); }
					Remember(vehicle, occupant);
					// Object.IsAlive object.c:517; Man.IsUnconscious man.c:63.
					// Unconscious recovery can re-enter a cached transport
					// (dayzplayerimplement.c:2389-2397), even with a stale command.
					if (!occupant.IsAlive() || occupant.IsUnconscious()) { return false; }
					HumanCommandVehicle command = occupant.GetCommand_Vehicle();
					if (!command) { return false; }
					if (command.GetTransport() != vehicle) { return false; }
					if (command.IsGettingIn() || command.IsSwitchSeat()) { return false; }
				}
				seat = seat + 1;
			}
			vehicleIndex = vehicleIndex + 1;
		}
		// Validate EVERY occupant before initiating any exit. A corpse/unconscious
		// occupant without a vehicle command refuses the whole delete, never a raw detach.
		int exitCount = m_Occupants.Count();
		CollectPending(tick);
		int occupantIndex = 0;
		while (occupantIndex < exitCount)
		{
			HumanCommandVehicle exitCommand = m_Occupants.Get(occupantIndex).GetCommand_Vehicle();
			if (!exitCommand) { return false; }
			if (!exitCommand.IsGettingOut()) { exitCommand.GetOutVehicle(); }
			occupantIndex = occupantIndex + 1;
		}
		m_Begun = true;
		return true;
	}

	bool Ready(int tick)
	{
		if (!m_Begun) { return false; }
		if (!Collect()) { m_ClearTick = -1; return false; }
		CollectPending(tick);
		if (!HasVehicles()) { return true; }
		bool occupied = false;
		int vehicleIndex = 0;
		while (vehicleIndex < m_Vehicles.Count())
		{
			Transport vehicle = m_Vehicles.Get(vehicleIndex);
			if (vehicle)
			{
				int seats = vehicle.CrewSize();
				if (seats < 0) { m_ClearTick = -1; return false; }
				int seat = 0;
				while (seat < seats)
				{
					Human occupant = vehicle.CrewMember(seat);
					if (occupant)
					{
						occupied = true;
						Remember(vehicle, occupant);
						// A late entrant is not silently ejected: wait or time out. Keep
						// tracking them even after the native crew slot becomes empty.
						if (m_Occupants.Find(occupant) < 0) { m_Occupants.Insert(occupant); }
					}
					seat = seat + 1;
				}
			}
			vehicleIndex = vehicleIndex + 1;
		}
		int occupantIndex = 0;
		while (occupantIndex < m_Occupants.Count())
		{
			Human priorOccupant = m_Occupants.Get(occupantIndex);
			vehicleIndex = 0;
			while (vehicleIndex < m_Vehicles.Count())
			{
				if (!IsClear(m_Vehicles.Get(vehicleIndex), priorOccupant))
				{
					occupied = true;
					Remember(m_Vehicles.Get(vehicleIndex), priorOccupant);
				}
				vehicleIndex = vehicleIndex + 1;
			}
			occupantIndex = occupantIndex + 1;
		}
		if (occupied) { m_ClearTick = -1; return false; }
		// A server tick after the exit has actually completed, not merely after
		// requesting it. This permits leave/HUD events before destruction; client
		// replication ordering still needs the explicitly pending in-game test.
		if (m_ClearTick < 0) { m_ClearTick = tick; return false; }
		return tick > m_ClearTick;
	}
}
