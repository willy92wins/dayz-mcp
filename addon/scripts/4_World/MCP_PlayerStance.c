// player_set_stance (dayz-mcp #413): both-sides ForceStance, the measured
// player_move route (MCP_PlayerMove.c:7-16). The server sends an RPC to the
// owner; the owner sends ScriptInputUserData; the server accepts it in
// OnInputUserDataProcess; owner and server apply GetCommand_Move().ForceStance
// inside CommandHandler. StartCommand_Move is never called from the bridge
// (it would cut the vanilla wake-up and swim/ladder/fall/climb on the server
// copy only; playerbase.c:3426-3434, Enfusion M1). HumanInputController has
// no OverrideStance (human.c:252-274).
//
// INPUT_UDT / RPC ids sit in the repo's 785410xx band, far from vanilla
// INPUT_UDT_* (1..16) and vanilla sync-juncture ids (0..26, 200).

const int MCP_INPUT_UDT_PLAYER_STANCE = 78541080;
const int MCP_RPC_PLAYER_STANCE = 78541081;

modded class PlayerBase
{
	protected bool m_MCPStancePending;
	protected bool m_MCPStanceWaitingOwner;
	protected bool m_MCPStanceIudRetry;
	protected bool m_MCPStanceUnlock;
	protected int m_MCPStanceId;
	protected int m_MCPStanceRequestId;
	protected int m_MCPStanceLastId;
	protected float m_MCPStanceDeadlineS;
	protected float m_MCPStanceUnlockDueS;
	protected bool m_MCPStanceRecoverRequested;
	protected string m_MCPStanceRequestedName;

	// Server entry from DispatchPlayerSetStance. Does not call ForceStance or
	// StartCommand_Move. Queues a both-sides apply and, when recover=true,
	// sends the vanilla unconsciousness juncture only.
	string MCPStanceBegin(string stance, bool recover)
	{
		int stanceId;
		string refusal;
		PlayerIdentity identity;
		stanceId = MCPStanceIdFromName(stance);
		if (stanceId < 0)
		{
			return "bad_args";
		}
		if (!IsAlive())
		{
			return "player_dead";
		}
		refusal = MCPStanceHardRefusal(stanceId);
		if (refusal != "")
		{
			return refusal;
		}
		if (IsUnconscious())
		{
			if (!recover)
			{
				return "still_unconscious";
			}
			DayZPlayerSyncJunctures.SendPlayerUnconsciousness(this, false);
		}
		else
		{
			refusal = MCPStanceSoftRefusal(stanceId);
			if (refusal != "")
			{
				return refusal;
			}
			if (!GetCommand_Move())
			{
				return "no_move_command";
			}
		}
		m_MCPStanceLastId = m_MCPStanceLastId + 1;
		m_MCPStanceRequestId = m_MCPStanceLastId;
		m_MCPStanceId = stanceId;
		m_MCPStanceRequestedName = stance;
		m_MCPStanceRecoverRequested = recover;
		m_MCPStanceDeadlineS = 0;
		if (GetGame())
		{
			m_MCPStanceDeadlineS = GetGame().GetTickTime() + 5.0;
		}
		if (!GetGame() || !GetGame().IsMultiplayer())
		{
			m_MCPStancePending = true;
			m_MCPStanceWaitingOwner = false;
			m_MCPStanceIudRetry = false;
			return "";
		}
		identity = GetIdentity();
		if (!identity)
		{
			return "no_identity";
		}
		if (!MCPStanceSendOwnerRpc(identity))
		{
			return "rpc_failed";
		}
		m_MCPStanceWaitingOwner = true;
		m_MCPStancePending = false;
		m_MCPStanceIudRetry = false;
		return "";
	}

	protected string MCPStanceHardRefusal(int stanceId)
	{
		EntityAI item;
		if (IsInVehicle())
		{
			return "in_vehicle";
		}
		if (IsSwimming())
		{
			return "swimming";
		}
		if (IsClimbingLadder() || IsClimbing())
		{
			return "climbing";
		}
		if (IsInWater())
		{
			return "in_water";
		}
		item = GetItemInHands();
		if (item && item.IsHeavyBehaviour() && stanceId != DayZPlayerConstants.STANCEIDX_ERECT)
		{
			return "heavy_item";
		}
		return "";
	}

	protected string MCPStanceSoftRefusal(int stanceId)
	{
		if (IsRestrained())
		{
			return "busy";
		}
		if (GetCommand_Action())
		{
			return "busy";
		}
		if (GetEmoteManager() && GetEmoteManager().IsEmotePlaying())
		{
			return "busy";
		}
		if (!DayZPlayerUtils.PlayerCanChangeStance(this, stanceId))
		{
			return "cannot_change_stance";
		}
		return "";
	}

	protected string MCPStanceApplyRefusal()
	{
		HumanCommandMove moveCmd;
		string refusal;
		refusal = MCPStanceHardRefusal(m_MCPStanceId);
		if (refusal != "")
		{
			return refusal;
		}
		refusal = MCPStanceSoftRefusal(m_MCPStanceId);
		if (refusal != "")
		{
			return refusal;
		}
		if (!IsAlive())
		{
			return "player_dead";
		}
		if (IsUnconscious())
		{
			return "still_unconscious";
		}
		moveCmd = GetCommand_Move();
		if (!moveCmd)
		{
			return "no_move_command";
		}
		if (moveCmd.IsChangingStance())
		{
			return "busy";
		}
		return "";
	}

	protected bool MCPStanceSendOwnerRpc(PlayerIdentity identity)
	{
		ScriptRPC rpc;
		rpc = new ScriptRPC();
		rpc.Write(m_MCPStanceRequestId);
		rpc.Write(m_MCPStanceId);
		rpc.Write(m_MCPStanceRecoverRequested);
		rpc.Send(this, MCP_RPC_PLAYER_STANCE, true, identity);
		return true;
	}

	protected void MCPStanceSendOwnerIud()
	{
		ScriptInputUserData message;
		int connGen;
		if (!GetGame() || !GetGame().IsMultiplayer() || !GetGame().IsClient())
		{
			m_MCPStanceIudRetry = false;
			return;
		}
		connGen = MCPConnGenValue();
		if (connGen == 0)
		{
			m_MCPStanceIudRetry = true;
			return;
		}
		if (!ScriptInputUserData.CanStoreInputUserData())
		{
			m_MCPStanceIudRetry = true;
			return;
		}
		message = new ScriptInputUserData();
		message.Write(MCP_INPUT_UDT_PLAYER_STANCE);
		message.Write(m_MCPStanceRequestId);
		message.Write(connGen);
		message.Write(m_MCPStanceId);
		message.Send();
		m_MCPStanceIudRetry = false;
	}

	override void OnRPC(PlayerIdentity sender, int rpc_type, ParamsReadContext ctx)
	{
		int requestId;
		int stanceId;
		bool recoverRequested;
		super.OnRPC(sender, rpc_type, ctx);
		if (rpc_type != MCP_RPC_PLAYER_STANCE)
		{
			return;
		}
		if (!GetGame() || !GetGame().IsClient() || GetGame().IsDedicatedServer())
		{
			return;
		}
		if (!ctx.Read(requestId) || !ctx.Read(stanceId) || !ctx.Read(recoverRequested))
		{
			return;
		}
		if (stanceId < 0)
		{
			MCPStanceCancel("owner_rpc_cancel");
			return;
		}
		if (stanceId != DayZPlayerConstants.STANCEIDX_ERECT && stanceId != DayZPlayerConstants.STANCEIDX_CROUCH && stanceId != DayZPlayerConstants.STANCEIDX_PRONE)
		{
			return;
		}
		m_MCPStanceRequestId = requestId;
		m_MCPStanceId = stanceId;
		m_MCPStanceRecoverRequested = recoverRequested;
		m_MCPStanceRequestedName = MCPStanceNameFromId(stanceId);
		m_MCPStancePending = true;
		if (GetGame())
		{
			m_MCPStanceDeadlineS = GetGame().GetTickTime() + 5.0;
		}
		MCPStanceSendOwnerIud();
	}

	override bool OnInputUserDataProcess(int userDataType, ParamsReadContext ctx)
	{
		int requestId;
		int connGen;
		int stanceId;
		if (userDataType != MCP_INPUT_UDT_PLAYER_STANCE)
		{
			return super.OnInputUserDataProcess(userDataType, ctx);
		}
		if (!GetGame() || !GetGame().IsServer())
		{
			return true;
		}
		if (!ctx.Read(requestId) || !ctx.Read(connGen) || !ctx.Read(stanceId))
		{
			Print("[DayZ_MCP] MCPStance uid= unknown requested= unknown observed= unknown changing=0 reason=read_failed");
			return true;
		}
		if (connGen == 0 || connGen != MCPConnGenValue())
		{
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=conn_gen_mismatch");
			return true;
		}
		if (stanceId != DayZPlayerConstants.STANCEIDX_ERECT && stanceId != DayZPlayerConstants.STANCEIDX_CROUCH && stanceId != DayZPlayerConstants.STANCEIDX_PRONE)
		{
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=bad_args");
			return true;
		}
		if (!m_MCPStanceWaitingOwner)
		{
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=not_waiting");
			return true;
		}
		if (requestId != m_MCPStanceRequestId || stanceId != m_MCPStanceId)
		{
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=request_mismatch");
			return true;
		}
		if (GetGame() && GetGame().GetTickTime() > m_MCPStanceDeadlineS)
		{
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=expired");
			m_MCPStanceWaitingOwner = false;
			return true;
		}
		m_MCPStancePending = true;
		m_MCPStanceWaitingOwner = false;
		return true;
	}

	override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)
	{
		super.CommandHandler(pDt, pCurrentCommandID, pCurrentCommandFinished);
		if (m_MCPStanceIudRetry)
		{
			MCPStanceSendOwnerIud();
		}
		MCPStanceOnCommandHandler();
	}

	protected void MCPStanceOnCommandHandler()
	{
		HumanCommandMove moveCmd;
		string refusal;
		if (m_MCPStanceUnlock)
		{
			MCPStanceTryUnlock();
		}
		if (m_MCPStanceWaitingOwner && GetGame() && GetGame().GetTickTime() > m_MCPStanceDeadlineS)
		{
			Print(MCPStanceLogLine("timeout") + " reason=no_owner_ack");
			m_MCPStanceWaitingOwner = false;
		}
		if (!m_MCPStancePending)
		{
			return;
		}
		if (GetGame() && GetGame().GetTickTime() > m_MCPStanceDeadlineS)
		{
			Print(MCPStanceLogLine("timeout") + " reason=timeout");
			m_MCPStancePending = false;
			return;
		}
		refusal = MCPStanceApplyRefusal();
		if (refusal == "no_move_command" || refusal == "still_unconscious" || refusal == "busy" || refusal == "cannot_change_stance")
		{
			return;
		}
		if (refusal != "")
		{
			Print(MCPStanceLogLine(refusal) + " reason=" + refusal);
			m_MCPStancePending = false;
			return;
		}
		moveCmd = GetCommand_Move();
		if (!moveCmd)
		{
			return;
		}
		moveCmd.ForceStance(m_MCPStanceId);
		m_MCPStancePending = false;
		m_MCPStanceUnlock = true;
		if (GetGame())
		{
			m_MCPStanceUnlockDueS = GetGame().GetTickTime() + 1.5;
			GetGame().GetCallQueue(CALL_CATEGORY_GAMEPLAY).CallLater(MCPStanceVerify, 1500, false);
		}
	}

	// After the transition, release the ForceStance lock (human.c:497-499,
	// ForceStance(-1)) so the player can change stance with input, and print
	// the post-CommandHandler observation (S1). Same-tick after is never used.
	void MCPStanceVerify()
	{
		HumanCommandMove moveCmd;
		string observed;
		int changing;
		GetMovementState(m_MovementState);
		observed = MCPStanceName();
		changing = 0;
		moveCmd = GetCommand_Move();
		if (moveCmd && moveCmd.IsChangingStance())
		{
			changing = 1;
		}
		Print(MCPStanceLogLine(observed) + " changing=" + changing.ToString());
		if (changing == 1)
		{
			if (GetGame())
			{
				GetGame().GetCallQueue(CALL_CATEGORY_GAMEPLAY).CallLater(MCPStanceVerify, 200, false);
			}
			return;
		}
		MCPStanceTryUnlock();
	}

	protected void MCPStanceTryUnlock()
	{
		HumanCommandMove moveCmd;
		if (!m_MCPStanceUnlock)
		{
			return;
		}
		if (GetGame() && GetGame().GetTickTime() < m_MCPStanceUnlockDueS)
		{
			return;
		}
		moveCmd = GetCommand_Move();
		if (moveCmd && moveCmd.IsChangingStance())
		{
			return;
		}
		if (moveCmd)
		{
			moveCmd.ForceStance(-1);
		}
		m_MCPStanceUnlock = false;
	}

	protected string MCPStanceLogLine(string observed)
	{
		string uid;
		PlayerIdentity identity;
		identity = GetIdentity();
		uid = "unknown";
		if (identity)
		{
			uid = identity.GetPlainId();
		}
		return "[DayZ_MCP] MCPStance uid=" + uid + " requested=" + m_MCPStanceRequestedName + " observed=" + observed;
	}

	bool MCPStanceActiveNow()
	{
		if (m_MCPStancePending || m_MCPStanceWaitingOwner || m_MCPStanceIudRetry || m_MCPStanceUnlock)
		{
			return true;
		}
		return false;
	}

	void MCPStanceCancel(string reason)
	{
		HumanCommandMove moveCmd;
		PlayerIdentity identity;
		if (!MCPStanceActiveNow())
		{
			return;
		}
		m_MCPStancePending = false;
		m_MCPStanceWaitingOwner = false;
		m_MCPStanceIudRetry = false;
		moveCmd = GetCommand_Move();
		if (moveCmd && !moveCmd.IsChangingStance())
		{
			moveCmd.ForceStance(-1);
			m_MCPStanceUnlock = false;
		}
		Print(MCPStanceLogLine("cancelled") + " reason=" + reason);
		if (GetGame() && GetGame().IsServer() && GetGame().IsMultiplayer())
		{
			identity = GetIdentity();
			if (identity)
			{
				MCPStanceSendCancelRpc(identity);
			}
		}
	}

	protected void MCPStanceSendCancelRpc(PlayerIdentity identity)
	{
		ScriptRPC rpc;
		rpc = new ScriptRPC();
		rpc.Write(m_MCPStanceRequestId);
		rpc.Write(-1);
		rpc.Write(false);
		rpc.Send(this, MCP_RPC_PLAYER_STANCE, true, identity);
	}

	bool MCPStanceCloseServer(int connGen)
	{
		if (connGen == 0 || connGen != MCPConnGenValue())
		{
			return false;
		}
		MCPStanceCancel("lease_close");
		return true;
	}

	string MCPStanceNameFromId(int stanceId)
	{
		if (stanceId == DayZPlayerConstants.STANCEIDX_ERECT || stanceId == DayZPlayerConstants.STANCEIDX_RAISEDERECT)
		{
			return "erect";
		}
		if (stanceId == DayZPlayerConstants.STANCEIDX_CROUCH || stanceId == DayZPlayerConstants.STANCEIDX_RAISEDCROUCH)
		{
			return "crouch";
		}
		if (stanceId == DayZPlayerConstants.STANCEIDX_PRONE || stanceId == DayZPlayerConstants.STANCEIDX_RAISEDPRONE)
		{
			return "prone";
		}
		return "unknown";
	}
};
