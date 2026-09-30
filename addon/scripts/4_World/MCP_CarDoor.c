// vehicle_door: one car door, resolved the way ActionCarDoorsOutside resolves the
// door a player looks at (actioncardoorsoutside.c:34-58). The door part is an
// attached CarDoor, and the first selection name of one of its components for
// which the car's GetAnimSourceFromSelection is not empty names the animation
// source. Static helpers only: nothing here writes a phase, and no entity is
// kept after a call returns.
class MCPCarDoorResolver
{
	// Components scanned per attached door part. The door models' component
	// counts are not verified; 64 is a margin, and a scan stops at its first match.
	static const int COMPONENT_SCAN_CAP = 64;

	// The source a component's names map to, "" when none does. Like the vanilla
	// loop (actioncardoorsoutside.c:41-58) it returns on the first name whose
	// source is not empty, and selection is that name.
	static string ComponentSource(CarScript car, CarDoor door, int component, TStringArray names, out string selection)
	{
		string componentSource = "";
		int nameScan = 0;

		selection = "";
		if (!car || !door || !names)
		{
			return "";
		}
		// Whether the native call appends to the list is not documented.
		names.Clear();
		door.GetActionComponentNameList(component, names);
		while (nameScan < names.Count())
		{
			componentSource = car.GetAnimSourceFromSelection(names.Get(nameScan));
			if (componentSource != "")
			{
				selection = names.Get(nameScan);
				return componentSource;
			}
			nameScan = nameScan + 1;
		}
		return "";
	}

	// The attached CarDoor whose component maps to wanted. Attachments are read
	// in inventory order, each door part's components from 0 up to
	// COMPONENT_SCAN_CAP, and the first match wins. False, with door null,
	// component -1 and selection "", when no attached door part maps to wanted.
	static bool Resolve(CarScript car, string wanted, out CarDoor door, out int component, out string selection)
	{
		GameInventory inventory = null;
		TStringArray names = null;
		CarDoor candidate = null;
		string candidateSelection = "";
		int attachmentScan = 0;
		int componentScan = 0;

		door = null;
		component = -1;
		selection = "";
		if (!car || wanted == "")
		{
			return false;
		}
		inventory = car.GetInventory();
		if (!inventory)
		{
			return false;
		}
		names = new TStringArray();
		while (attachmentScan < inventory.AttachmentCount())
		{
			candidate = CarDoor.Cast(inventory.GetAttachmentFromIndex(attachmentScan));
			if (candidate)
			{
				componentScan = 0;
				while (componentScan < COMPONENT_SCAN_CAP)
				{
					if (ComponentSource(car, candidate, componentScan, names, candidateSelection) == wanted)
					{
						door = candidate;
						component = componentScan;
						selection = candidateSelection;
						return true;
					}
					componentScan = componentScan + 1;
				}
			}
			attachmentScan = attachmentScan + 1;
		}
		return false;
	}

	// The slot the door part sits in, read as CarDoor.CanDetachAttachment reads
	// it (inventoryitem.c:450-457). "" when the part is not in an attachment slot.
	static string SlotName(CarDoor door)
	{
		GameInventory doorInventory = null;
		InventoryLocation location = null;

		if (!door)
		{
			return "";
		}
		doorInventory = door.GetInventory();
		if (!doorInventory)
		{
			return "";
		}
		location = new InventoryLocation();
		if (!doorInventory.GetCurrentInventoryLocation(location))
		{
			return "";
		}
		if (location.GetSlot() == -1)
		{
			return "";
		}
		return InventorySlots.GetSlotName(location.GetSlot());
	}

	// The slot of a crew door's part: the seat of the source
	// (transport.c:529-548), then the car's slot for that seat (carscript.c:2700,
	// the sedan's at civiliansedan.c:271-290). "" for a source with no seat, such
	// as the hood or the trunk, or a car that names no slot for that seat.
	static string CrewDoorSlot(CarScript car, string source)
	{
		int seat = -1;

		if (!car)
		{
			return "";
		}
		seat = car.GetSeatIndexFromDoor(source);
		if (seat < 0)
		{
			return "";
		}
		return car.GetDoorInvSlotNameFromSeatPos(seat);
	}

	// GetCarDoorsState returns an int (carscript.c:2796); DOORS_OPEN means the
	// phase is above 0.5 (carscript.c:2801-2811). Anything else reads missing.
	static string StateName(int doorState)
	{
		if (doorState == CarDoorState.DOORS_OPEN)
		{
			return "open";
		}
		if (doorState == CarDoorState.DOORS_CLOSED)
		{
			return "closed";
		}
		return "missing";
	}
}
