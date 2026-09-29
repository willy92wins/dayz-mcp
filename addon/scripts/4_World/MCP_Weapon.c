// Above ERPCs.RPC_END (erpcs.c:207). DIAG_DEVELOPER shifts that enum; this does not.
const int MCP_RPC_HANDS_TAKE = 78541063;

// EEFired is an engine event on the side that simulates the shot
// (weapon_base.c:341). Particles run only when not a dedicated server;
// the obsolete health path was gated on IsServer. Script ints do not
// replicate, and weapon_state reads the server entity, so only the
// server tally is meaningful. Increment is a field add: no alloc per shot.
modded class Weapon_Base
{
	protected int m_MCPShotCount;

	override void EEFired(int muzzleType, int mode, string ammoType)
	{
		super.EEFired(muzzleType, mode, ammoType);
		if (!GetGame())
		{
			return;
		}
		if (!GetGame().IsServer())
		{
			return;
		}
		m_MCPShotCount = m_MCPShotCount + 1;
	}

	int MCPShotCount()
	{
		return m_MCPShotCount;
	}
}

modded class PlayerBase
{
	override void OnRPC(PlayerIdentity sender, int rpc_type, ParamsReadContext ctx)
	{
		super.OnRPC(sender, rpc_type, ctx);
		if (rpc_type != MCP_RPC_HANDS_TAKE)
		{
			return;
		}
		// Dedicated server must not take: ActionTakeItemToHands bails there,
		// and PredictiveTakeEntityToHands needs the input-user-data channel.
		if (!GetGame())
		{
			return;
		}
		if (!GetGame().IsClient())
		{
			return;
		}
		if (GetGame().IsDedicatedServer())
		{
			return;
		}
		MCPApplyHandsTakeRpc(ctx);
	}

	// validatedType is the classname the server checked. Serializer.Write(string)
	// is the same call notificationsystem.c uses. Returns false when the RPC
	// was not sent; the caller must not report accepted.
	bool MCPRequestTakeToHands(EntityAI item, string validatedType)
	{
		if (!item)
		{
			return false;
		}
		if (validatedType == "")
		{
			return false;
		}
		PlayerIdentity identity = GetIdentity();
		if (!identity)
		{
			return false;
		}
		int netLow = 0;
		int netHigh = 0;
		item.GetNetworkID(netLow, netHigh);
		ScriptRPC rpc = new ScriptRPC();
		if (!rpc.Write(netLow))
		{
			return false;
		}
		if (!rpc.Write(netHigh))
		{
			return false;
		}
		if (!rpc.Write(validatedType))
		{
			return false;
		}
		rpc.Send(this, MCP_RPC_HANDS_TAKE, true, identity);
		return true;
	}

	// Same predicates DispatchHandsTake used, so the client recheck cannot
	// drift from the server. Own cargo/attachment, a BaseBuildingBase
	// attachment that can detach, or ground within UAMaxDistances.DEFAULT
	// (actionconstants.c:112).
	string MCPHandsTakeRefusal(ItemBase item)
	{
		if (!item)
		{
			return "not_an_item";
		}
		if (!item.IsTakeable())
		{
			return "not_takeable";
		}
		if (item.IsBeingPlaced())
		{
			return "not_takeable";
		}
		if (item.IsSetForDeletion())
		{
			return "not_takeable";
		}

		EntityAI held = GetEntityInHands();
		if (held == item)
		{
			return "already_in_hands";
		}

		string reachError = MCPHandsTakeReachError(item);
		if (reachError != "")
		{
			return reachError;
		}
		if (!item.CanPutIntoHands(this))
		{
			return "cannot_take";
		}
		if (held)
		{
			if (!GameInventory.CanSwapEntitiesEx(held, item))
			{
				return "hands_blocked";
			}
			return "";
		}

		GameInventory inventory = GetInventory();
		if (!inventory)
		{
			return "cannot_take";
		}
		if (!inventory.CanAddEntityIntoHands(item))
		{
			return "cannot_take";
		}
		return "";
	}

	protected string MCPHandsTakeReachError(ItemBase item)
	{
		int locType = InventoryLocationType.UNKNOWN;
		bool located = false;
		InventoryLocation loc = new InventoryLocation();
		GameInventory itemInventory = item.GetInventory();
		if (itemInventory)
		{
			located = itemInventory.GetCurrentInventoryLocation(loc);
		}
		if (located)
		{
			if (loc.IsValid())
			{
				locType = loc.GetType();
			}
		}

		if (locType == InventoryLocationType.HANDS)
		{
			if (loc.GetParent() == this)
			{
				return "already_in_hands";
			}
			return "not_reachable";
		}

		Man owner = item.GetHierarchyRootPlayer();
		if (owner == this)
		{
			if (locType == InventoryLocationType.CARGO)
			{
				return "";
			}
			if (locType == InventoryLocationType.PROXYCARGO)
			{
				return "";
			}
			if (locType == InventoryLocationType.ATTACHMENT)
			{
				return MCPHandsTakeAttachmentError(item);
			}
			return "not_reachable";
		}

		EntityAI parent = item.GetHierarchyParent();
		if (parent)
		{
			if (!BaseBuildingBase.Cast(parent))
			{
				return "not_reachable";
			}
			string detachError = MCPHandsTakeAttachmentError(item);
			if (detachError != "")
			{
				return detachError;
			}
		}

		if (locType == InventoryLocationType.TEMP)
		{
			return "not_reachable";
		}
		if (locType == InventoryLocationType.VEHICLE)
		{
			return "not_reachable";
		}
		if (locType == InventoryLocationType.CARGO)
		{
			return "not_reachable";
		}
		if (locType == InventoryLocationType.PROXYCARGO)
		{
			return "not_reachable";
		}
		if (!MCPHandsTakeWithinReach(item))
		{
			return "not_reachable";
		}
		return "";
	}

	protected string MCPHandsTakeAttachmentError(ItemBase item)
	{
		EntityAI parent = item.GetHierarchyParent();
		if (!parent)
		{
			return "not_reachable";
		}
		if (!item.CanDetachAttachment(parent))
		{
			return "not_reachable";
		}
		if (!parent.CanReleaseAttachment(item))
		{
			return "not_reachable";
		}
		return "";
	}

	protected bool MCPHandsTakeWithinReach(ItemBase item)
	{
		float reach = UAMaxDistances.DEFAULT;
		float reachSq = reach * reach;
		float distSq = vector.DistanceSq(GetPosition(), item.GetPosition());
		if (distSq > reachSq)
		{
			return false;
		}
		return true;
	}

	protected void MCPDropHandsTake(string reason)
	{
		Print("[DayZ_MCP] hands_take dropped reason=" + reason);
	}

	protected void MCPApplyHandsTakeRpc(ParamsReadContext ctx)
	{
		int netLow = 0;
		int netHigh = 0;
		string expectedType = "";
		if (!ctx.Read(netLow))
		{
			MCPDropHandsTake("read_failed");
			return;
		}
		if (!ctx.Read(netHigh))
		{
			MCPDropHandsTake("read_failed");
			return;
		}
		if (!ctx.Read(expectedType))
		{
			MCPDropHandsTake("read_failed");
			return;
		}
		if (netLow == 0)
		{
			if (netHigh == 0)
			{
				MCPDropHandsTake("not_networked");
				return;
			}
		}
		if (!ScriptInputUserData.CanStoreInputUserData())
		{
			MCPDropHandsTake("input_busy");
			return;
		}
		Object resolved = GetGame().GetObjectByNetworkId(netLow, netHigh);
		if (!resolved)
		{
			MCPDropHandsTake("not_streamed");
			return;
		}
		ItemBase item = ItemBase.Cast(resolved);
		if (!item)
		{
			MCPDropHandsTake("not_an_item");
			return;
		}
		// Compare the stored string. A GetType() == comparison is a known
		// validator warning, and a recycled network id of another class must
		// not pass. GetPersistentID (entityai.c:3375) is not sent: this tree
		// cannot show the client replica carries the same four ints for a
		// world_spawn that has not been saved, and a mismatch would refuse
		// every legitimate take.
		string seenType = item.GetType();
		if (expectedType == "")
		{
			MCPDropHandsTake("type_mismatch");
			return;
		}
		if (seenType != expectedType)
		{
			MCPDropHandsTake("type_mismatch");
			return;
		}
		string refusal = MCPHandsTakeRefusal(item);
		if (refusal != "")
		{
			MCPDropHandsTake(refusal);
			return;
		}
		PredictiveTakeEntityToHands(item);
	}
}
