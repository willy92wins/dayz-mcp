// inventory_move (fb-20261024-212316-17fa). The owning client runs the same
// predictive calls a drag in the inventory UI runs. The server reads the
// location afterwards; this file only issues the call.
const int MCP_RPC_INVENTORY_MOVE = 78541071;
const int MCP_RPC_INVENTORY_MOVE_REFUSED = 78541072;

modded class PlayerBase
{
	bool MCPRequestInventoryMove(int jobId, EntityAI item, string itemType, EntityAI target, string targetType, string op, string slotName)
	{
		if (!item)
		{
			return false;
		}
		if (itemType == "")
		{
			return false;
		}
		if (op == "")
		{
			return false;
		}
		PlayerIdentity identity = GetIdentity();
		if (!identity)
		{
			return false;
		}
		int itemLow = 0;
		int itemHigh = 0;
		item.GetNetworkID(itemLow, itemHigh);
		int targetLow = 0;
		int targetHigh = 0;
		if (target)
		{
			target.GetNetworkID(targetLow, targetHigh);
		}
		ScriptRPC rpc = new ScriptRPC();
		if (!rpc.Write(jobId))
		{
			return false;
		}
		if (!rpc.Write(itemLow))
		{
			return false;
		}
		if (!rpc.Write(itemHigh))
		{
			return false;
		}
		if (!rpc.Write(itemType))
		{
			return false;
		}
		if (!rpc.Write(targetLow))
		{
			return false;
		}
		if (!rpc.Write(targetHigh))
		{
			return false;
		}
		if (!rpc.Write(targetType))
		{
			return false;
		}
		if (!rpc.Write(op))
		{
			return false;
		}
		if (!rpc.Write(slotName))
		{
			return false;
		}
		rpc.Send(this, MCP_RPC_INVENTORY_MOVE, true, identity);
		return true;
	}

	protected bool MCPInventoryMoveOwnedByOther(EntityAI entity)
	{
		if (!entity)
		{
			return false;
		}
		Man root = entity.GetHierarchyRootPlayer();
		if (!root)
		{
			return false;
		}
		if (root == this)
		{
			return false;
		}
		return true;
	}

	protected void MCPDropInventoryMove(string reason)
	{
		Print("[DayZ_MCP] inventory_move dropped reason=" + reason);
	}

	// The id was read, so the server can match this refusal to the job.
	protected void MCPRefuseInventoryMove(int jobId, string reason)
	{
		MCPDropInventoryMove(reason);
		ScriptRPC rpc = new ScriptRPC();
		if (!rpc.Write(jobId))
		{
			return;
		}
		if (!rpc.Write(reason))
		{
			return;
		}
		rpc.Send(this, MCP_RPC_INVENTORY_MOVE_REFUSED, true, null);
	}

	// Empty string means the client call was issued. A non-empty string is a
	// refusal and nothing was called.
	string MCPPerformClientInventoryMove(string op, EntityAI item, EntityAI target, string slotName)
	{
		if (!GetGame())
		{
			return "not_client";
		}
		if (GetGame().IsDedicatedServer())
		{
			return "not_client";
		}
		if (!item)
		{
			return "object_not_found";
		}
		if (!ScriptInputUserData.CanStoreInputUserData())
		{
			return "input_busy";
		}
		// Another player's worn or carried item is never moved. The server
		// applies the same check before the RPC. A refusal after the job id
		// is read is sent back on MCP_RPC_INVENTORY_MOVE_REFUSED. Only a
		// failure to read that id stays local and ends as a server timeout.
		if (MCPInventoryMoveOwnedByOther(item))
		{
			return "not_reachable";
		}
		if (MCPInventoryMoveOwnedByOther(target))
		{
			return "not_reachable";
		}
		ItemBase itemBase = ItemBase.Cast(item);
		if (op == "to_cargo")
		{
			if (!target)
			{
				return "object_not_found";
			}
			if (!PredictiveTakeEntityToTargetCargo(target, item))
			{
				return "not_reachable";
			}
			return "";
		}
		if (op == "to_attachment")
		{
			if (!target)
			{
				return "object_not_found";
			}
			int slotId = InventorySlots.GetSlotIdFromString(slotName);
			if (!InventorySlots.IsSlotIdValid(slotId))
			{
				return "slot_not_found";
			}
			if (!PredictiveTakeEntityToTargetAttachmentEx(target, item, slotId))
			{
				return "not_reachable";
			}
			return "";
		}
		if (op == "to_hands")
		{
			PredictiveTakeEntityToHands(item);
			return "";
		}
		if (op == "to_inventory")
		{
			if (!PredictiveTakeEntityToInventory(FindInventoryLocationType.ANY, item))
			{
				return "no_room";
			}
			return "";
		}
		if (op == "combine")
		{
			ItemBase receiver = ItemBase.Cast(target);
			if (!receiver)
			{
				return "object_not_found";
			}
			if (!itemBase)
			{
				return "not_an_item";
			}
			receiver.CombineItemsClient(itemBase, true);
			return "";
		}
		if (op == "split_half")
		{
			if (!itemBase)
			{
				return "not_an_item";
			}
			itemBase.OnRightClick();
			return "";
		}
		if (op == "split_to_hands")
		{
			if (!itemBase)
			{
				return "not_an_item";
			}
			itemBase.SplitIntoStackMaxHandsClient(this);
			return "";
		}
		return "bad_args";
	}

	protected void MCPApplyInventoryMoveRpc(ParamsReadContext ctx)
	{
		int jobId = 0;
		int itemLow = 0;
		int itemHigh = 0;
		string itemType = "";
		int targetLow = 0;
		int targetHigh = 0;
		string targetType = "";
		string op = "";
		string slotName = "";
		if (!ctx.Read(jobId))
		{
			// No id was read, so there is no job to answer.
			MCPDropInventoryMove("read_failed");
			return;
		}
		if (!ctx.Read(itemLow))
		{
			MCPRefuseInventoryMove(jobId, "read_failed");
			return;
		}
		if (!ctx.Read(itemHigh))
		{
			MCPRefuseInventoryMove(jobId, "read_failed");
			return;
		}
		if (!ctx.Read(itemType))
		{
			MCPRefuseInventoryMove(jobId, "read_failed");
			return;
		}
		if (!ctx.Read(targetLow))
		{
			MCPRefuseInventoryMove(jobId, "read_failed");
			return;
		}
		if (!ctx.Read(targetHigh))
		{
			MCPRefuseInventoryMove(jobId, "read_failed");
			return;
		}
		if (!ctx.Read(targetType))
		{
			MCPRefuseInventoryMove(jobId, "read_failed");
			return;
		}
		if (!ctx.Read(op))
		{
			MCPRefuseInventoryMove(jobId, "read_failed");
			return;
		}
		if (!ctx.Read(slotName))
		{
			MCPRefuseInventoryMove(jobId, "read_failed");
			return;
		}
		Object resolvedItem = GetGame().GetObjectByNetworkId(itemLow, itemHigh);
		EntityAI item = EntityAI.Cast(resolvedItem);
		if (!item)
		{
			MCPRefuseInventoryMove(jobId, "not_streamed");
			return;
		}
		string seenType = item.GetType();
		if (seenType != itemType)
		{
			MCPRefuseInventoryMove(jobId, "type_mismatch");
			return;
		}
		EntityAI target = null;
		if (targetLow != 0 || targetHigh != 0)
		{
			Object resolvedTarget = GetGame().GetObjectByNetworkId(targetLow, targetHigh);
			target = EntityAI.Cast(resolvedTarget);
			if (!target)
			{
				MCPRefuseInventoryMove(jobId, "not_streamed");
				return;
			}
			string seenTarget = target.GetType();
			if (seenTarget != targetType)
			{
				MCPRefuseInventoryMove(jobId, "type_mismatch");
				return;
			}
		}
		string refusal = MCPPerformClientInventoryMove(op, item, target, slotName);
		if (refusal != "")
		{
			MCPRefuseInventoryMove(jobId, refusal);
		}
	}

	override void OnRPC(PlayerIdentity sender, int rpc_type, ParamsReadContext ctx)
	{
		super.OnRPC(sender, rpc_type, ctx);
		if (rpc_type != MCP_RPC_INVENTORY_MOVE)
		{
			return;
		}
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
		MCPApplyInventoryMoveRpc(ctx);
	}
}
