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

	void MCPRequestTakeToHands(EntityAI item)
	{
		if (!item)
		{
			return;
		}
		PlayerIdentity identity = GetIdentity();
		if (!identity)
		{
			return;
		}
		int netLow = 0;
		int netHigh = 0;
		item.GetNetworkID(netLow, netHigh);
		ScriptRPC rpc = new ScriptRPC();
		rpc.Write(netLow);
		rpc.Write(netHigh);
		rpc.Send(this, MCP_RPC_HANDS_TAKE, true, identity);
	}

	protected void MCPApplyHandsTakeRpc(ParamsReadContext ctx)
	{
		int netLow = 0;
		int netHigh = 0;
		if (!ctx.Read(netLow))
		{
			Print("[DayZ_MCP] hands_take dropped reason=read_failed");
			return;
		}
		if (!ctx.Read(netHigh))
		{
			Print("[DayZ_MCP] hands_take dropped reason=read_failed");
			return;
		}
		if (netLow == 0)
		{
			if (netHigh == 0)
			{
				Print("[DayZ_MCP] hands_take dropped reason=not_networked");
				return;
			}
		}
		if (!ScriptInputUserData.CanStoreInputUserData())
		{
			Print("[DayZ_MCP] hands_take dropped reason=input_busy");
			return;
		}
		Object resolved = GetGame().GetObjectByNetworkId(netLow, netHigh);
		if (!resolved)
		{
			Print("[DayZ_MCP] hands_take dropped reason=not_streamed");
			return;
		}
		EntityAI entity = EntityAI.Cast(resolved);
		if (!entity)
		{
			Print("[DayZ_MCP] hands_take dropped reason=not_an_item");
			return;
		}
		PredictiveTakeEntityToHands(entity);
	}
}
