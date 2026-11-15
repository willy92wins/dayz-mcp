// player_set_stance (dayz-mcp #413): both-sides ForceStance, the measured
// player_move route (MCP_PlayerMove.c:7-16). The server sends an RPC to the
// owner; the owner checks its apply guards and HumanCommandMove in
// CommandHandler before sending ScriptInputUserData and applying
// GetCommand_Move().ForceStance in that same step; the server accepts it in
// OnInputUserDataProcess and applies
// ForceStance inside its own CommandHandler. StartCommand_Move is never
// called from the bridge (it would cut the vanilla wake-up and
// swim/ladder/fall/climb on the server copy only; playerbase.c:3426-3434,
// Enfusion M1). HumanInputController has no OverrideStance (human.c:252-274).
//
// INPUT_UDT / RPC ids sit in the repo's 785410xx band, far from vanilla
// INPUT_UDT_* (1..17) and vanilla sync-juncture ids (0..26, 200).

const int MCP_INPUT_UDT_PLAYER_STANCE = 78541080;
const int MCP_RPC_PLAYER_STANCE = 78541081;
const int MCP_STANCE_IUD_CANCEL = -1;
const int MCP_STANCE_IUD_CANCEL_APPLIED = -2;
const int MCP_STANCE_RPC_CANCEL = -1;
const int MCP_STANCE_RPC_APPLIED = -2;
const float MCP_STANCE_TIMEOUT_S = 5.0;
const float MCP_STANCE_CANCEL_TIMEOUT_S = 1.5;
const float MCP_STANCE_UNLOCK_S = 1.5;
const float MCP_STANCE_VERIFY_S = 1.5;

modded class PlayerBase
{
	protected bool m_MCPStancePending;
	protected bool m_MCPStanceWaitingOwner;
	protected bool m_MCPStanceWaitingApplyAck;
	protected bool m_MCPStanceIudRetry;
	protected bool m_MCPStanceUnlock;
	protected bool m_MCPStanceCancelOutstanding;
	protected bool m_MCPStanceVerifyArmed;
	protected bool m_MCPStanceSendWanted;
	protected bool m_MCPStanceCancelWanted;
	protected bool m_MCPStanceFromIud;
	protected bool m_MCPStanceReleaseAfterApply;
	protected int m_MCPStanceId;
	protected int m_MCPStanceRequestId;
	protected int m_MCPStanceLastId;
	protected int m_MCPStanceClosedFloor;
	protected int m_MCPStanceOwnerClosedFloor;
	protected int m_MCPStanceOwnerAckedFloor;
	protected int m_MCPStanceOwnerAppliedId;
	protected int m_MCPStanceServerAppliedFloor;
	protected int m_MCPStanceAppliedConnGen;
	protected int m_MCPStanceAcceptedId;
	protected int m_MCPStanceCancelRequestId;
	protected int m_MCPStanceCancelTargetId;
	protected float m_MCPStanceElapsedS;
	protected float m_MCPStanceCancelElapsedS;
	protected float m_MCPStanceUnlockElapsedS;
	protected float m_MCPStanceVerifyElapsedS;
	protected float m_MCPStanceVerifyDueS;
	protected bool m_MCPStanceRecoverRequested;
	protected string m_MCPStanceRequestedName;
	protected string m_MCPStanceCancelReason;

	protected bool MCPStanceBusyNow()
	{
		bool unlockActive;
		unlockActive = false;
		if (m_MCPStanceUnlock)
		{
			if (IsAlive() || GetCommand_Move())
			{
				unlockActive = true;
			}
		}
		if (m_MCPStancePending || m_MCPStanceWaitingOwner || m_MCPStanceWaitingApplyAck || m_MCPStanceIudRetry || m_MCPStanceCancelOutstanding || unlockActive || m_MCPStanceSendWanted || m_MCPStanceCancelWanted)
		{
			return true;
		}
		return false;
	}

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
		if (MCPStanceBusyNow())
		{
			return "busy";
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
		m_MCPStanceElapsedS = 0;
		m_MCPStanceFromIud = false;
		m_MCPStanceReleaseAfterApply = false;
		m_MCPStanceWaitingApplyAck = false;
		m_MCPStanceVerifyArmed = false;
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

	// player_set_stance server entry (MCPBridge.c DispatchPlayerSetStance).
	// Declared here, after MCPStanceBegin, not in MCP_PlayerCare.c: an earlier
	// World file cannot call a PlayerBase method a later file declares
	// (tools/tests/test_enforce_module_order.py). Queues a both-sides
	// ForceStance and does not write health or shock, kill, or respawn.
	// recover=true may request
	// DayZPlayerSyncJunctures.SendPlayerUnconsciousness(this, false).
	// StartCommand_Move is never called from this tool.
	string MCPSetStanceServer(string stance, bool recover)
	{
		return MCPStanceBegin(stance, recover);
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
		if (GetDayZPlayerInventory() && GetDayZPlayerInventory().IsProcessing())
		{
			return "busy";
		}
		if (GetWeaponManager() && GetWeaponManager().IsRunning())
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
		if (!IsAlive())
		{
			return "player_dead";
		}
		if (IsUnconscious())
		{
			return "still_unconscious";
		}
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

	protected void MCPStanceSendApplyAckRpc(PlayerIdentity identity, int requestId, bool releaseNow)
	{
		ScriptRPC rpc;
		if (!identity || requestId <= 0)
		{
			return;
		}
		rpc = new ScriptRPC();
		rpc.Write(requestId);
		rpc.Write(MCP_STANCE_RPC_APPLIED);
		rpc.Write(releaseNow);
		rpc.Send(this, MCP_RPC_PLAYER_STANCE, true, identity);
	}

	protected bool MCPStanceSendOwnerIud()
	{
		ScriptInputUserData message;
		int connGen;
		if (!GetGame() || !GetGame().IsMultiplayer() || !GetGame().IsClient())
		{
			return false;
		}
		connGen = MCPConnGenValue();
		if (connGen == 0)
		{
			return false;
		}
		if (!ScriptInputUserData.CanStoreInputUserData())
		{
			return false;
		}
		message = new ScriptInputUserData();
		message.Write(MCP_INPUT_UDT_PLAYER_STANCE);
		message.Write(m_MCPStanceRequestId);
		message.Write(connGen);
		message.Write(m_MCPStanceId);
		message.Send();
		m_MCPStanceAppliedConnGen = connGen;
		return true;
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
		if (stanceId == MCP_STANCE_RPC_APPLIED)
		{
			if (requestId <= 0)
			{
				return;
			}
			if (requestId > m_MCPStanceServerAppliedFloor)
			{
				m_MCPStanceServerAppliedFloor = requestId;
			}
			if (m_MCPStanceWaitingApplyAck && requestId >= m_MCPStanceOwnerAppliedId)
			{
				m_MCPStanceWaitingApplyAck = false;
				if (m_MCPStanceUnlock)
				{
					if (recoverRequested || m_MCPStanceOwnerAppliedId <= m_MCPStanceClosedFloor)
					{
						m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
					}
					else
					{
						m_MCPStanceUnlockElapsedS = 0;
					}
				}
			}
			return;
		}
		if (stanceId == MCP_STANCE_RPC_CANCEL)
		{
			if (requestId <= 0 || requestId <= m_MCPStanceClosedFloor)
			{
				return;
			}
			if (m_MCPStanceCancelWanted && requestId <= m_MCPStanceCancelTargetId)
			{
				return;
			}
			if (requestId < m_MCPStanceRequestId)
			{
				m_MCPStanceClosedFloor = requestId;
				return;
			}
			if (requestId > m_MCPStanceRequestId)
			{
				m_MCPStanceRequestId = requestId;
				m_MCPStanceSendWanted = false;
				m_MCPStancePending = false;
			}
			m_MCPStanceCancelTargetId = requestId;
			MCPStanceCancel("owner_rpc_cancel");
			return;
		}
		if (stanceId < 0)
		{
			return;
		}
		if (requestId <= m_MCPStanceClosedFloor || requestId <= m_MCPStanceRequestId)
		{
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=closed");
			return;
		}
		if (stanceId != DayZPlayerConstants.STANCEIDX_ERECT && stanceId != DayZPlayerConstants.STANCEIDX_CROUCH && stanceId != DayZPlayerConstants.STANCEIDX_PRONE)
		{
			return;
		}
		if (m_MCPStanceWaitingApplyAck && m_MCPStanceOwnerAppliedId < requestId)
		{
			m_MCPStanceWaitingApplyAck = false;
			if (m_MCPStanceOwnerAppliedId > m_MCPStanceServerAppliedFloor)
			{
				m_MCPStanceServerAppliedFloor = m_MCPStanceOwnerAppliedId;
			}
			if (m_MCPStanceUnlock)
			{
				m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
			}
		}
		if (m_MCPStanceCancelTargetId > 0 && m_MCPStanceCancelTargetId < requestId)
		{
			if (m_MCPStanceCancelTargetId > m_MCPStanceClosedFloor)
			{
				m_MCPStanceClosedFloor = m_MCPStanceCancelTargetId;
			}
			if (m_MCPStanceCancelWanted && m_MCPStanceUnlock)
			{
				m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
			}
		}
		m_MCPStanceCancelWanted = false;
		m_MCPStanceCancelTargetId = 0;
		m_MCPStanceCancelReason = "";
		m_MCPStanceIudRetry = false;
		m_MCPStanceRequestId = requestId;
		m_MCPStanceId = stanceId;
		m_MCPStanceRecoverRequested = recoverRequested;
		m_MCPStanceRequestedName = MCPStanceNameFromId(stanceId);
		m_MCPStanceSendWanted = true;
		m_MCPStancePending = false;
		m_MCPStanceElapsedS = 0;
	}

	override bool OnInputUserDataProcess(int userDataType, ParamsReadContext ctx)
	{
		int requestId;
		int connGen;
		int stanceId;
		bool appliedByOwner;
		bool releaseDup;
		PlayerIdentity identity;
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
		if (stanceId == MCP_STANCE_IUD_CANCEL || stanceId == MCP_STANCE_IUD_CANCEL_APPLIED)
		{
			if (requestId > m_MCPStanceClosedFloor)
			{
				m_MCPStanceClosedFloor = requestId;
			}
			if (requestId > m_MCPStanceOwnerAckedFloor)
			{
				m_MCPStanceOwnerAckedFloor = requestId;
			}
			if (m_MCPStanceCancelOutstanding && (m_MCPStanceCancelRequestId == 0 || requestId >= m_MCPStanceCancelRequestId))
			{
				m_MCPStanceCancelOutstanding = false;
				m_MCPStanceCancelRequestId = 0;
				m_MCPStanceCancelElapsedS = 0;
			}
			appliedByOwner = false;
			if (stanceId == MCP_STANCE_IUD_CANCEL_APPLIED)
			{
				appliedByOwner = true;
			}
			else if (m_MCPStanceFromIud && m_MCPStanceAcceptedId == m_MCPStanceRequestId)
			{
				appliedByOwner = true;
			}
			if (appliedByOwner)
			{
				if (m_MCPStanceRequestId > 0 && requestId >= m_MCPStanceRequestId)
				{
					if (m_MCPStancePending)
					{
						m_MCPStanceReleaseAfterApply = true;
					}
					m_MCPStanceWaitingOwner = false;
					if (m_MCPStanceUnlock)
					{
						m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
					}
				}
			}
			else
			{
				if (requestId > m_MCPStanceOwnerClosedFloor)
				{
					m_MCPStanceOwnerClosedFloor = requestId;
				}
				if (m_MCPStanceRequestId > 0 && m_MCPStanceRequestId <= m_MCPStanceOwnerClosedFloor)
				{
					m_MCPStancePending = false;
					m_MCPStanceWaitingOwner = false;
					m_MCPStanceFromIud = false;
					m_MCPStanceReleaseAfterApply = false;
					m_MCPStanceVerifyArmed = false;
				}
				if (m_MCPStanceUnlock && requestId >= m_MCPStanceRequestId && m_MCPStanceRequestId <= m_MCPStanceClosedFloor)
				{
					m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
				}
			}
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=cancel_ack");
			return true;
		}
		if (stanceId != DayZPlayerConstants.STANCEIDX_ERECT && stanceId != DayZPlayerConstants.STANCEIDX_CROUCH && stanceId != DayZPlayerConstants.STANCEIDX_PRONE)
		{
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=bad_args");
			return true;
		}
		identity = GetIdentity();
		if (requestId <= m_MCPStanceOwnerClosedFloor)
		{
			if (identity && requestId > 0)
			{
				MCPStanceSendApplyAckRpc(identity, requestId, true);
			}
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=closed");
			return true;
		}
		if (requestId != m_MCPStanceRequestId || stanceId != m_MCPStanceId)
		{
			if (identity && requestId > 0)
			{
				MCPStanceSendApplyAckRpc(identity, requestId, true);
			}
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=request_mismatch");
			return true;
		}
		if (requestId == m_MCPStanceAcceptedId)
		{
			if (!m_MCPStancePending && identity && requestId > 0)
			{
				releaseDup = false;
				if (requestId <= m_MCPStanceClosedFloor)
				{
					releaseDup = true;
				}
				MCPStanceSendApplyAckRpc(identity, requestId, releaseDup);
			}
			Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=duplicate");
			return true;
		}
		if (!m_MCPStanceWaitingOwner)
		{
			if (requestId <= m_MCPStanceClosedFloor)
			{
				m_MCPStanceReleaseAfterApply = true;
				Print(MCPStanceLogLine("queued") + " request=" + requestId.ToString() + " reason=applied_after_close");
			}
			else
			{
				if (identity && requestId > 0)
				{
					MCPStanceSendApplyAckRpc(identity, requestId, true);
				}
				Print("[DayZ_MCP] MCPStance request=" + requestId.ToString() + " reason=not_waiting");
				return true;
			}
		}
		m_MCPStancePending = true;
		m_MCPStanceWaitingOwner = false;
		m_MCPStanceFromIud = true;
		m_MCPStanceAcceptedId = requestId;
		return true;
	}

	override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)
	{
		super.CommandHandler(pDt, pCurrentCommandID, pCurrentCommandFinished);
		MCPStanceOnCommandHandler(pDt);
	}

	protected void MCPStanceOnCommandHandler(float pDt)
	{
		HumanCommandMove moveCmd;
		PlayerIdentity identity;
		string refusal;
		bool ownerReadyToApply;
		bool wasFromIud;
		bool releaseAfterApply;
		int ackRequestId;
		ownerReadyToApply = false;
		if (m_MCPStanceWaitingApplyAck)
		{
			if (m_MCPStanceAppliedConnGen == 0 || MCPConnGenValue() != m_MCPStanceAppliedConnGen)
			{
				m_MCPStanceWaitingApplyAck = false;
			}
		}
		if (!IsAlive())
		{
			m_MCPStanceWaitingApplyAck = false;
			m_MCPStanceVerifyArmed = false;
			if (GetGame() && GetGame().IsServer() && m_MCPStanceWaitingOwner)
			{
				MCPStanceCancel("player_dead");
			}
		}
		if (m_MCPStanceWaitingOwner || m_MCPStancePending || m_MCPStanceSendWanted)
		{
			m_MCPStanceElapsedS += pDt;
		}
		if (m_MCPStanceUnlock && !m_MCPStanceWaitingApplyAck)
		{
			m_MCPStanceUnlockElapsedS += pDt;
		}
		if (m_MCPStanceCancelOutstanding)
		{
			if (GetGame() && GetGame().IsMultiplayer() && GetGame().IsServer() && !GetIdentity())
			{
				m_MCPStanceCancelOutstanding = false;
				m_MCPStanceCancelRequestId = 0;
				m_MCPStanceCancelElapsedS = 0;
			}
			else
			{
				m_MCPStanceCancelElapsedS += pDt;
				if (m_MCPStanceCancelElapsedS > MCP_STANCE_CANCEL_TIMEOUT_S)
				{
					if (m_MCPStanceAcceptedId < m_MCPStanceCancelRequestId && m_MCPStanceCancelRequestId > m_MCPStanceOwnerClosedFloor)
					{
						m_MCPStanceOwnerClosedFloor = m_MCPStanceCancelRequestId;
					}
					if (m_MCPStanceCancelRequestId > m_MCPStanceOwnerAckedFloor)
					{
						m_MCPStanceOwnerAckedFloor = m_MCPStanceCancelRequestId;
					}
					Print("[DayZ_MCP] MCPStance request=" + m_MCPStanceCancelRequestId.ToString() + " reason=no_cancel_ack");
					m_MCPStanceCancelOutstanding = false;
					m_MCPStanceCancelRequestId = 0;
					m_MCPStanceCancelElapsedS = 0;
				}
			}
		}
		if (m_MCPStanceVerifyArmed)
		{
			m_MCPStanceVerifyElapsedS += pDt;
			if (m_MCPStanceVerifyElapsedS >= m_MCPStanceVerifyDueS)
			{
				MCPStanceVerify();
			}
		}
		if (m_MCPStanceUnlock)
		{
			MCPStanceTryUnlock();
		}
		if (m_MCPStanceCancelWanted)
		{
			MCPStanceOwnerCancelStep();
			if (m_MCPStanceUnlock)
			{
				MCPStanceTryUnlock();
			}
		}
		if (m_MCPStanceIudRetry && !m_MCPStanceCancelWanted)
		{
			MCPStanceSendCancelIud();
		}
		if (m_MCPStanceSendWanted && !m_MCPStanceCancelWanted)
		{
			if (m_MCPStanceElapsedS > MCP_STANCE_TIMEOUT_S)
			{
				m_MCPStanceCancelTargetId = m_MCPStanceRequestId;
				m_MCPStanceCancelWanted = true;
				m_MCPStanceCancelReason = "timeout";
				MCPStanceOwnerCancelStep();
				if (m_MCPStanceUnlock)
				{
					MCPStanceTryUnlock();
				}
			}
			else
			{
				refusal = MCPStanceApplyRefusal();
				if (refusal == "")
				{
					moveCmd = GetCommand_Move();
					if (!moveCmd)
					{
						refusal = "no_move_command";
					}
					else if (moveCmd.IsChangingStance())
					{
						refusal = "busy";
					}
				}
				if (refusal == "no_move_command" || refusal == "still_unconscious" || refusal == "busy" || refusal == "cannot_change_stance")
				{
					// Transient impediment: keep SendWanted and do not emit start IUD.
				}
				else if (refusal != "")
				{
					Print(MCPStanceLogLine(refusal) + " reason=" + refusal);
					m_MCPStanceSendWanted = false;
					m_MCPStanceCancelTargetId = m_MCPStanceRequestId;
					m_MCPStanceCancelWanted = true;
					m_MCPStanceCancelReason = refusal;
					MCPStanceOwnerCancelStep();
					if (m_MCPStanceUnlock)
					{
						MCPStanceTryUnlock();
					}
				}
				else if (MCPStanceSendOwnerIud())
				{
					m_MCPStanceSendWanted = false;
					m_MCPStancePending = true;
					ownerReadyToApply = true;
				}
			}
		}
		if (m_MCPStanceWaitingOwner && GetGame() && GetGame().IsMultiplayer() && GetGame().IsServer() && !GetIdentity())
		{
			MCPStanceCancel("owner_gone");
		}
		if (m_MCPStanceWaitingOwner && m_MCPStanceElapsedS > MCP_STANCE_TIMEOUT_S)
		{
			MCPStanceCancel("no_owner_ack");
		}
		if (!m_MCPStancePending)
		{
			return;
		}
		if (!ownerReadyToApply)
		{
			if (m_MCPStanceElapsedS > MCP_STANCE_TIMEOUT_S)
			{
				if (!GetGame() || !GetGame().IsMultiplayer())
				{
					MCPStanceCancel("timeout");
					return;
				}
				if (GetGame().IsClient() && !GetGame().IsDedicatedServer())
				{
					if (!m_MCPStanceCancelWanted)
					{
						m_MCPStanceCancelTargetId = m_MCPStanceRequestId;
						m_MCPStanceCancelWanted = true;
						m_MCPStanceCancelReason = "timeout";
						MCPStanceOwnerCancelStep();
					}
					if (!m_MCPStancePending)
					{
						return;
					}
				}
				else if (!GetIdentity())
				{
					MCPStanceCancel("owner_gone");
					return;
				}
				else if (!m_MCPStanceFromIud)
				{
					MCPStanceCancel("timeout");
					return;
				}
			}
			refusal = MCPStanceApplyRefusal();
			if (refusal == "no_move_command" || refusal == "still_unconscious" || refusal == "busy" || refusal == "cannot_change_stance")
			{
				return;
			}
			if (refusal != "")
			{
				if (refusal != "player_dead" && GetGame() && GetGame().IsMultiplayer() && m_MCPStanceFromIud && GetIdentity())
				{
					return;
				}
				Print(MCPStanceLogLine(refusal) + " reason=" + refusal);
				wasFromIud = m_MCPStanceFromIud;
				ackRequestId = m_MCPStanceAcceptedId;
				if (ackRequestId <= 0)
				{
					ackRequestId = m_MCPStanceRequestId;
				}
				m_MCPStancePending = false;
				m_MCPStanceFromIud = false;
				m_MCPStanceReleaseAfterApply = false;
				if (wasFromIud && GetGame() && GetGame().IsMultiplayer() && GetGame().IsServer())
				{
					identity = GetIdentity();
					if (identity)
					{
						MCPStanceSendApplyAckRpc(identity, ackRequestId, true);
					}
				}
				if (GetGame() && GetGame().IsMultiplayer() && GetGame().IsClient() && !GetGame().IsDedicatedServer())
				{
					m_MCPStanceCancelTargetId = m_MCPStanceRequestId;
					m_MCPStanceCancelWanted = true;
					m_MCPStanceCancelReason = refusal;
					MCPStanceOwnerCancelStep();
					if (m_MCPStanceUnlock)
					{
						MCPStanceTryUnlock();
					}
				}
				return;
			}
			moveCmd = GetCommand_Move();
			if (!moveCmd)
			{
				return;
			}
		}
		releaseAfterApply = m_MCPStanceReleaseAfterApply;
		StopHandEvent();
		moveCmd.ForceStance(m_MCPStanceId);
		if (GetGame() && GetGame().IsMultiplayer() && GetGame().IsClient() && !GetGame().IsDedicatedServer())
		{
			m_MCPStanceOwnerAppliedId = m_MCPStanceRequestId;
			if (m_MCPStanceServerAppliedFloor < m_MCPStanceOwnerAppliedId)
			{
				m_MCPStanceWaitingApplyAck = true;
			}
			else
			{
				m_MCPStanceWaitingApplyAck = false;
			}
		}
		if (GetGame() && GetGame().IsMultiplayer() && GetGame().IsServer())
		{
			identity = GetIdentity();
			if (identity)
			{
				MCPStanceSendApplyAckRpc(identity, m_MCPStanceRequestId, releaseAfterApply);
			}
		}
		m_MCPStancePending = false;
		m_MCPStanceFromIud = false;
		m_MCPStanceUnlock = true;
		m_MCPStanceUnlockElapsedS = 0;
		if (m_MCPStanceReleaseAfterApply)
		{
			m_MCPStanceReleaseAfterApply = false;
			m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
		}
		m_MCPStanceVerifyArmed = true;
		m_MCPStanceVerifyElapsedS = 0;
		m_MCPStanceVerifyDueS = MCP_STANCE_VERIFY_S;
	}

	protected void MCPStanceOwnerCancelStep()
	{
		int targetId;
		targetId = m_MCPStanceCancelTargetId;
		if (targetId <= 0)
		{
			targetId = m_MCPStanceRequestId;
		}
		if (targetId <= 0)
		{
			m_MCPStanceCancelWanted = false;
			m_MCPStanceIudRetry = false;
			return;
		}
		if (targetId < m_MCPStanceRequestId)
		{
			if (targetId > m_MCPStanceClosedFloor)
			{
				m_MCPStanceClosedFloor = targetId;
			}
			m_MCPStanceCancelWanted = false;
			m_MCPStanceCancelTargetId = 0;
			m_MCPStanceIudRetry = false;
			return;
		}
		m_MCPStanceSendWanted = false;
		if (targetId > m_MCPStanceClosedFloor)
		{
			m_MCPStanceClosedFloor = targetId;
		}
		if (!MCPStanceSendCancelIud())
		{
			return;
		}
		m_MCPStancePending = false;
		m_MCPStanceCancelWanted = false;
		m_MCPStanceCancelTargetId = 0;
		m_MCPStanceIudRetry = false;
		m_MCPStanceVerifyArmed = false;
		if (m_MCPStanceUnlock)
		{
			m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
		}
		Print(MCPStanceLogLine("cancelled") + " reason=" + m_MCPStanceCancelReason);
	}

	// Post-CommandHandler observation log only (S-2). Same-tick after is never
	// used; lock release lives in MCPStanceTryUnlock inside CommandHandler.
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
		if (GetGame() && GetGame().IsServer())
		{
			Print(MCPStanceLogLine(observed) + " changing=" + changing.ToString());
		}
		if (changing == 1)
		{
			m_MCPStanceVerifyElapsedS = 0;
			m_MCPStanceVerifyDueS = 0.2;
			return;
		}
		m_MCPStanceVerifyArmed = false;
	}

	protected void MCPStanceTryUnlock()
	{
		HumanCommandMove moveCmd;
		bool bodyDeadOrAbandoned;
		if (!m_MCPStanceUnlock)
		{
			return;
		}
		bodyDeadOrAbandoned = !IsAlive();
		if (GetGame() && GetGame().IsMultiplayer() && GetGame().IsServer() && !GetIdentity())
		{
			bodyDeadOrAbandoned = true;
		}
		if (bodyDeadOrAbandoned)
		{
			moveCmd = GetCommand_Move();
			if (!moveCmd)
			{
				m_MCPStanceUnlock = false;
				m_MCPStanceWaitingApplyAck = false;
				m_MCPStanceVerifyArmed = false;
				return;
			}
			moveCmd.ForceStance(-1);
			m_MCPStanceUnlock = false;
			m_MCPStanceWaitingApplyAck = false;
			m_MCPStanceVerifyArmed = false;
			return;
		}
		if (m_MCPStanceWaitingApplyAck)
		{
			return;
		}
		if (m_MCPStanceUnlockElapsedS < MCP_STANCE_UNLOCK_S)
		{
			return;
		}
		moveCmd = GetCommand_Move();
		if (!moveCmd)
		{
			return;
		}
		if (moveCmd.IsChangingStance())
		{
			return;
		}
		moveCmd.ForceStance(-1);
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
		bool unlockActive;
		unlockActive = false;
		if (m_MCPStanceUnlock)
		{
			if (IsAlive() || GetCommand_Move())
			{
				unlockActive = true;
			}
		}
		if (m_MCPStancePending || m_MCPStanceWaitingOwner || m_MCPStanceWaitingApplyAck || m_MCPStanceIudRetry || unlockActive || m_MCPStanceCancelOutstanding || m_MCPStanceSendWanted || m_MCPStanceCancelWanted)
		{
			return true;
		}
		return false;
	}

	void MCPStanceCancel(string reason)
	{
		PlayerIdentity identity;
		bool wasActive;
		bool hadUnlock;
		bool hadRequest;
		bool keepPending;
		bool wasFromIud;
		bool cancelAlreadyInFlight;
		int ackRequestId;
		wasActive = MCPStanceActiveNow() || m_MCPStanceUnlock || m_MCPStanceWaitingApplyAck;
		hadUnlock = m_MCPStanceUnlock;
		hadRequest = wasActive || m_MCPStanceRequestId > 0 || m_MCPStanceClosedFloor > 0;
		if (GetGame() && GetGame().IsClient() && GetGame().IsMultiplayer() && !GetGame().IsDedicatedServer())
		{
			if (!IsAlive())
			{
				m_MCPStanceWaitingApplyAck = false;
				m_MCPStanceVerifyArmed = false;
				if (!GetCommand_Move())
				{
					m_MCPStanceUnlock = false;
					hadUnlock = false;
				}
			}
			if (reason != "owner_rpc_cancel")
			{
				if (!wasActive)
				{
					return;
				}
				if (m_MCPStanceRequestId <= 0)
				{
					if (hadUnlock)
					{
						m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
					}
					return;
				}
				if (m_MCPStanceRequestId > m_MCPStanceCancelTargetId)
				{
					m_MCPStanceCancelTargetId = m_MCPStanceRequestId;
				}
			}
			if (m_MCPStanceCancelTargetId <= 0 || m_MCPStanceCancelTargetId <= m_MCPStanceClosedFloor)
			{
				if (hadUnlock && !m_MCPStanceWaitingApplyAck)
				{
					m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
				}
				return;
			}
			m_MCPStanceCancelWanted = true;
			m_MCPStanceCancelReason = reason;
			return;
		}
		if (!hadRequest)
		{
			return;
		}
		if (m_MCPStanceRequestId > m_MCPStanceClosedFloor)
		{
			m_MCPStanceClosedFloor = m_MCPStanceRequestId;
		}
		wasFromIud = m_MCPStanceFromIud;
		ackRequestId = m_MCPStanceAcceptedId;
		if (ackRequestId <= 0)
		{
			ackRequestId = m_MCPStanceRequestId;
		}
		keepPending = false;
		if (GetGame() && GetGame().IsMultiplayer() && m_MCPStanceFromIud && GetIdentity())
		{
			if (IsAlive())
			{
				keepPending = true;
				m_MCPStanceReleaseAfterApply = true;
			}
		}
		if (!keepPending)
		{
			m_MCPStancePending = false;
			m_MCPStanceFromIud = false;
			m_MCPStanceReleaseAfterApply = false;
			m_MCPStanceVerifyArmed = false;
			if (wasFromIud && GetGame() && GetGame().IsServer() && GetGame().IsMultiplayer())
			{
				identity = GetIdentity();
				if (identity)
				{
					MCPStanceSendApplyAckRpc(identity, ackRequestId, true);
				}
			}
		}
		m_MCPStanceWaitingOwner = false;
		m_MCPStanceIudRetry = false;
		if (hadUnlock)
		{
			if (!IsAlive() && !GetCommand_Move())
			{
				m_MCPStanceUnlock = false;
			}
			else
			{
				m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
			}
		}
		cancelAlreadyInFlight = m_MCPStanceCancelOutstanding && m_MCPStanceClosedFloor > 0 && m_MCPStanceCancelRequestId >= m_MCPStanceClosedFloor;
		if (wasActive && !cancelAlreadyInFlight)
		{
			Print(MCPStanceLogLine("cancelled") + " reason=" + reason);
		}
		if (GetGame() && GetGame().IsServer() && GetGame().IsMultiplayer() && wasActive && !cancelAlreadyInFlight)
		{
			identity = GetIdentity();
			if (identity && (m_MCPStanceClosedFloor == 0 || m_MCPStanceOwnerAckedFloor < m_MCPStanceClosedFloor))
			{
				MCPStanceSendCancelRpc(identity);
				m_MCPStanceCancelOutstanding = true;
				m_MCPStanceCancelRequestId = m_MCPStanceClosedFloor;
				m_MCPStanceCancelElapsedS = 0;
			}
		}
	}

	protected bool MCPStanceSendCancelIud()
	{
		ScriptInputUserData message;
		int connGen;
		int cancelMarker;
		if (!GetGame() || !GetGame().IsMultiplayer() || !GetGame().IsClient())
		{
			m_MCPStanceIudRetry = false;
			return false;
		}
		connGen = MCPConnGenValue();
		if (connGen == 0 || !ScriptInputUserData.CanStoreInputUserData())
		{
			if (!IsAlive())
			{
				m_MCPStanceIudRetry = false;
				return true;
			}
			m_MCPStanceIudRetry = true;
			return false;
		}
		cancelMarker = MCP_STANCE_IUD_CANCEL;
		if (m_MCPStanceClosedFloor > 0 && m_MCPStanceOwnerAppliedId == m_MCPStanceClosedFloor)
		{
			cancelMarker = MCP_STANCE_IUD_CANCEL_APPLIED;
		}
		message = new ScriptInputUserData();
		message.Write(MCP_INPUT_UDT_PLAYER_STANCE);
		message.Write(m_MCPStanceClosedFloor);
		message.Write(connGen);
		message.Write(cancelMarker);
		message.Send();
		m_MCPStanceIudRetry = false;
		return true;
	}

	protected void MCPStanceSendCancelRpc(PlayerIdentity identity)
	{
		ScriptRPC rpc;
		rpc = new ScriptRPC();
		rpc.Write(m_MCPStanceClosedFloor);
		rpc.Write(MCP_STANCE_RPC_CANCEL);
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

	override void OnReconnect()
	{
		super.OnReconnect();
		m_MCPStanceCancelOutstanding = false;
		m_MCPStanceCancelRequestId = 0;
		m_MCPStanceCancelElapsedS = 0;
		m_MCPStanceWaitingOwner = false;
		m_MCPStanceWaitingApplyAck = false;
		m_MCPStancePending = false;
		m_MCPStanceFromIud = false;
		m_MCPStanceReleaseAfterApply = false;
		m_MCPStanceSendWanted = false;
		m_MCPStanceCancelWanted = false;
		m_MCPStanceCancelTargetId = 0;
		m_MCPStanceIudRetry = false;
		m_MCPStanceVerifyArmed = false;
		m_MCPStanceElapsedS = 0;
		if (m_MCPStanceUnlock)
		{
			if (!IsAlive() && !GetCommand_Move())
			{
				m_MCPStanceUnlock = false;
			}
			else
			{
				m_MCPStanceUnlockElapsedS = MCP_STANCE_UNLOCK_S;
			}
		}
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
