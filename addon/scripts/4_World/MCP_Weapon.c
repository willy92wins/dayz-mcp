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

// Client-only weapon overrides. ENABLED raise always has a deadline.
// ONE_FRAME aim is forced DISABLED after the command tick that consumes
// it, and again on restore, death, a player change, or a replacement.
// Fire is not an override: WeaponManager.Fire runs from CommandHandler
// (weapon_base.c:291 allows ProcessWeaponEvent only there or in HandleWeapons).
// HandleWeapons itself runs before this script, and a true return from
// ModCommandHandlerInside aborts the rest (dayzplayerimplement.c:2410),
// so the shot is consumed after super.
class MCPWeaponControl
{
	// Same deadman as vehicle_control (3s / 30s). 3s is past the 0.2s raise
	// blend and short enough that a dropped client does not leave the weapon up.
	static const float RAISE_DEFAULT_TTL_S = 3.0;
	static const float RAISE_MAX_TTL_S = 30.0;
	// Cap on the float passed to OverrideAimChangeX/Y. Those protos name no
	// unit. GetAimChange is radians (human.c:31). 3.141593 is pi rounded up
	// so one call cannot exceed a half-turn if the engine uses that unit.
	static const float AIM_CHANGE_ABS_MAX = 3.141593;

	static int s_SimTick;
	static bool s_AwaitTick;

	static bool s_RaiseArmed;
	static float s_RaiseDeadlineS;
	static PlayerBase s_RaisePlayer;
	static int s_RaiseGen;
	static string s_RaiseAbort;

	static bool s_AimOutstanding;
	static PlayerBase s_AimPlayer;
	static int s_AimGen;
	static int s_AimSimAt;
	static string s_AimAbort;

	static bool s_FirePending;
	static bool s_FireDone;
	static bool s_FireAccepted;
	static PlayerBase s_FirePlayer;
	static int s_FireGen;
	static int s_FireSimAt;
	static string s_FireReason;
	static string s_FireAbort;

	static bool s_SightsWatch;
	static PlayerBase s_SightsPlayer;
	static int s_SightsGen;
	static string s_SightsAbort;

	static bool IsBusy()
	{
		if (s_RaiseArmed)
		{
			return true;
		}
		if (s_AimOutstanding)
		{
			return true;
		}
		if (s_FirePending)
		{
			return true;
		}
		if (s_SightsWatch)
		{
			return true;
		}
		if (s_AwaitTick)
		{
			return true;
		}
		return false;
	}

	static int SimTick()
	{
		return s_SimTick;
	}

	static float RaiseDeadlineS()
	{
		return s_RaiseDeadlineS;
	}

	static bool FireAccepted()
	{
		return s_FireAccepted;
	}

	static string FireReason()
	{
		return s_FireReason;
	}

	static int Generation(string verb)
	{
		if (verb == "weapon_raise")
		{
			return s_RaiseGen;
		}
		if (verb == "weapon_aim")
		{
			return s_AimGen;
		}
		if (verb == "weapon_fire")
		{
			return s_FireGen;
		}
		if (verb == "weapon_sights")
		{
			return s_SightsGen;
		}
		return -1;
	}

	static string Abort(string verb)
	{
		if (verb == "weapon_raise")
		{
			return s_RaiseAbort;
		}
		if (verb == "weapon_aim")
		{
			return s_AimAbort;
		}
		if (verb == "weapon_fire")
		{
			return s_FireAbort;
		}
		if (verb == "weapon_sights")
		{
			return s_SightsAbort;
		}
		return "";
	}

	static void DisableRaiseOn(PlayerBase player)
	{
		HumanInputController hic;
		if (player)
		{
			hic = player.GetInputController();
			if (hic)
			{
				hic.OverrideRaise(HumanInputControllerOverrideType.DISABLED, false);
			}
		}
		s_RaiseArmed = false;
		s_RaisePlayer = null;
	}

	static void DisableAimOn(PlayerBase player)
	{
		HumanInputController hic;
		if (player)
		{
			hic = player.GetInputController();
			if (hic)
			{
				hic.OverrideAimChangeX(HumanInputControllerOverrideType.DISABLED, 0.0);
				hic.OverrideAimChangeY(HumanInputControllerOverrideType.DISABLED, 0.0);
			}
		}
		s_AimOutstanding = false;
		s_AimPlayer = null;
	}

	static void ReleaseAll(string why)
	{
		DisableRaiseOn(s_RaisePlayer);
		DisableAimOn(s_AimPlayer);
		s_FirePending = false;
		s_FirePlayer = null;
		s_FireDone = false;
		s_SightsWatch = false;
		s_SightsPlayer = null;
		s_AwaitTick = false;
		s_RaiseAbort = why;
		s_AimAbort = why;
		s_FireAbort = why;
		s_SightsAbort = why;
		s_RaiseGen = s_RaiseGen + 1;
		s_AimGen = s_AimGen + 1;
		s_FireGen = s_FireGen + 1;
		s_SightsGen = s_SightsGen + 1;
	}

	// Clears a one-tick watch when ITS job ends. A newer call has a newer
	// generation and must keep its own pending fire or sights watch.
	static void FinishWatch(string verb, int generation)
	{
		if (verb == "weapon_aim")
		{
			if (generation == s_AimGen)
			{
				DisableAimOn(s_AimPlayer);
			}
		}
		if (verb == "weapon_fire")
		{
			if (generation == s_FireGen)
			{
				s_FirePending = false;
				s_FirePlayer = null;
			}
		}
		if (verb == "weapon_sights")
		{
			if (generation == s_SightsGen)
			{
				s_SightsWatch = false;
				s_SightsPlayer = null;
			}
		}
	}

	static int BeginRaise(PlayerBase player, float ttlS)
	{
		float now;
		HumanInputController hic;
		s_RaiseAbort = "";
		s_RaiseGen = s_RaiseGen + 1;
		DisableRaiseOn(s_RaisePlayer);
		now = 0.0;
		if (GetGame())
		{
			now = GetGame().GetTickTime();
		}
		hic = player.GetInputController();
		if (hic)
		{
			hic.OverrideRaise(HumanInputControllerOverrideType.ENABLED, true);
		}
		s_RaisePlayer = player;
		s_RaiseDeadlineS = now + ttlS;
		s_RaiseArmed = true;
		s_AwaitTick = true;
		return s_RaiseGen;
	}

	static int BeginRelease(PlayerBase player)
	{
		float now;
		s_RaiseAbort = "";
		s_RaiseGen = s_RaiseGen + 1;
		DisableRaiseOn(s_RaisePlayer);
		DisableRaiseOn(player);
		now = 0.0;
		if (GetGame())
		{
			now = GetGame().GetTickTime();
		}
		s_RaiseDeadlineS = now;
		s_AwaitTick = true;
		return s_RaiseGen;
	}

	static int BeginAim(PlayerBase player, float dx, float dy)
	{
		HumanInputController hic;
		s_AimAbort = "";
		s_AimGen = s_AimGen + 1;
		DisableAimOn(s_AimPlayer);
		hic = player.GetInputController();
		if (hic)
		{
			hic.OverrideAimChangeX(HumanInputControllerOverrideType.ONE_FRAME, dx);
			hic.OverrideAimChangeY(HumanInputControllerOverrideType.ONE_FRAME, dy);
		}
		s_AimPlayer = player;
		s_AimOutstanding = true;
		s_AimSimAt = s_SimTick;
		s_AwaitTick = true;
		return s_AimGen;
	}

	static int BeginFire(PlayerBase player)
	{
		s_FireAbort = "";
		s_FireGen = s_FireGen + 1;
		s_FirePlayer = player;
		s_FirePending = true;
		s_FireDone = false;
		s_FireAccepted = false;
		s_FireReason = "";
		s_FireSimAt = s_SimTick;
		s_AwaitTick = true;
		return s_FireGen;
	}

	static int BeginSights(PlayerBase player)
	{
		s_SightsAbort = "";
		s_SightsGen = s_SightsGen + 1;
		s_SightsPlayer = player;
		s_SightsWatch = true;
		s_AwaitTick = true;
		return s_SightsGen;
	}

	static void OnCommandHandler(PlayerBase player)
	{
		PlayerBase live;
		if (!IsBusy())
		{
			return;
		}
		if (!player)
		{
			ReleaseAll("no_player");
			return;
		}
		if (!GetGame())
		{
			ReleaseAll("no_player");
			return;
		}
		// GetPlayer() returns DayZPlayer (game.c:946). Identity check needs PlayerBase.Cast.
		live = PlayerBase.Cast(GetGame().GetPlayer());
		if (player != live)
		{
			return;
		}
		if (!player.IsAlive())
		{
			ReleaseAll("player_dead");
			return;
		}
		if (s_RaiseArmed && s_RaisePlayer != player)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (s_AimOutstanding && s_AimPlayer != player)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (s_FirePending && s_FirePlayer != player)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (s_SightsWatch && s_SightsPlayer != player)
		{
			ReleaseAll("player_changed");
			return;
		}
		s_SimTick = s_SimTick + 1;
		s_AwaitTick = false;
		if (s_FirePending)
		{
			if (s_SimTick > s_FireSimAt)
			{
				ConsumeFire(player);
			}
		}
		MaintainRaise(player);
		MaintainAim(player);
	}

	static void MaintainFromTick()
	{
		PlayerBase live;
		if (!IsBusy())
		{
			return;
		}
		live = null;
		if (GetGame())
		{
			live = PlayerBase.Cast(GetGame().GetPlayer());
		}
		if (!live)
		{
			ReleaseAll("no_player");
			return;
		}
		if (!live.IsAlive())
		{
			ReleaseAll("player_dead");
			return;
		}
		if (s_RaiseArmed && s_RaisePlayer != live)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (s_AimOutstanding && s_AimPlayer != live)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (s_FirePending && s_FirePlayer != live)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (s_SightsWatch && s_SightsPlayer != live)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (!s_RaiseArmed)
		{
			return;
		}
		if (GetGame().GetTickTime() > s_RaiseDeadlineS)
		{
			DisableRaiseOn(live);
		}
	}

	static void MaintainRaise(PlayerBase player)
	{
		HumanInputController hic;
		if (!s_RaiseArmed)
		{
			return;
		}
		if (!GetGame())
		{
			return;
		}
		if (GetGame().GetTickTime() > s_RaiseDeadlineS)
		{
			DisableRaiseOn(player);
			return;
		}
		hic = player.GetInputController();
		if (!hic)
		{
			return;
		}
		hic.OverrideRaise(HumanInputControllerOverrideType.ENABLED, true);
	}

	static void MaintainAim(PlayerBase player)
	{
		if (!s_AimOutstanding)
		{
			return;
		}
		if (s_SimTick > s_AimSimAt)
		{
			DisableAimOn(player);
		}
	}

	// WeaponManager.CanFire (weaponmanager.c:79) then Weapon_Base.CanFire
	// (weapon_base.c:1268). Both are bools, so the reason is the first
	// failing public clause. Empty, fired-out and jammed refuse here:
	// WeaponManager.Fire would still dry-fire those chambers.
	static void ConsumeFire(PlayerBase player)
	{
		Weapon_Base held;
		WeaponManager manager;
		DayZPlayerInventory inventory;
		int muzzle;

		s_FirePending = false;
		s_FireAccepted = false;
		s_FireReason = "";
		s_FireDone = true;
		if (!player)
		{
			s_FireReason = "no_player";
			return;
		}
		held = Weapon_Base.Cast(player.GetEntityInHands());
		if (!held)
		{
			s_FireReason = "no_weapon_in_hands";
			return;
		}
		if (player.IsLiftWeapon())
		{
			s_FireReason = "weapon_lifted";
			return;
		}
		if (!player.IsRaised())
		{
			s_FireReason = "not_raised";
			return;
		}
		if (held.IsDamageDestroyed())
		{
			s_FireReason = "weapon_destroyed";
			return;
		}
		inventory = player.GetDayZPlayerInventory();
		if (!inventory)
		{
			s_FireReason = "inventory_processing";
			return;
		}
		if (inventory.IsProcessing())
		{
			s_FireReason = "inventory_processing";
			return;
		}
		if (!player.IsWeaponRaiseCompleted())
		{
			s_FireReason = "raise_not_completed";
			return;
		}
		if (player.IsFighting())
		{
			s_FireReason = "fighting";
			return;
		}
		if (held.IsCoolDown())
		{
			s_FireReason = "cooldown";
			return;
		}
		muzzle = held.GetCurrentMuzzle();
		if (held.IsChamberEmpty(muzzle))
		{
			s_FireReason = "chamber_empty";
			return;
		}
		if (held.IsChamberFiredOut(muzzle))
		{
			s_FireReason = "chamber_fired_out";
			return;
		}
		if (held.IsJammed())
		{
			s_FireReason = "jammed";
			return;
		}
		if (!held.CanFire())
		{
			s_FireReason = "weapon_lifted";
			return;
		}
		manager = player.GetWeaponManager();
		if (!manager)
		{
			s_FireReason = "cannot_fire";
			return;
		}
		if (!manager.CanFire(held))
		{
			s_FireReason = "cannot_fire";
			return;
		}
		// CanFire (weaponmanager.c:79-87) skips these. Same predicates as
		// dispatch: IsAlive (object.c:523), IsUnconscious (playerbase.c:3655),
		// IsRestrained (playerbase.c:2040), IsInVehicle (dayzplayerimplement.c:465).
		if (!player.IsAlive())
		{
			s_FireReason = "player_dead";
			return;
		}
		if (player.IsUnconscious())
		{
			s_FireReason = "player_unconscious";
			return;
		}
		if (player.IsRestrained())
		{
			s_FireReason = "player_restrained";
			return;
		}
		if (player.IsInVehicle())
		{
			s_FireReason = "player_in_vehicle";
			return;
		}
		manager.Fire(held);
		s_FireAccepted = true;
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

	// Local player only, and only while an override or a one-tick read is
	// outstanding. super runs HandleWeapons first; the shot is after that.
	override void CommandHandler(float pDt, int pCurrentCommandID, bool pCurrentCommandFinished)
	{
		PlayerBase live;
		super.CommandHandler(pDt, pCurrentCommandID, pCurrentCommandFinished);
		if (!MCPWeaponControl.IsBusy())
		{
			return;
		}
		if (!GetGame())
		{
			return;
		}
		// GetPlayer() returns DayZPlayer (game.c:946). Identity check needs PlayerBase.Cast.
		live = PlayerBase.Cast(GetGame().GetPlayer());
		if (this != live)
		{
			return;
		}
		MCPWeaponControl.OnCommandHandler(this);
	}
}
