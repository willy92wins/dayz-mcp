// player_heal (inbox bef7) and player_godmode (inbox 3136) on the server's
// PlayerBase. 4_World, beside the other modded PlayerBase files; config.cpp
// compiles the whole folder. MCPBridge dispatches both verbs; MissionServer
// applies the default godmode when a player connects or respawns and runs the
// godmode top-up from its OnUpdate (MissionServer.c).

// Godmode: SetAllowDamage(false) on the PlayerBase (object.c:1187-1192), the
// switch of the diag menu's invincibility and of the benchmark mission
// (plugindiagmenu.c:688-695, missionbenchmark.c:373). ON by default: every body
// an identity gets, a new or a loaded character and a respawn's new body alike,
// goes through MissionServer.InvokeOnConnect (missionserver.c:316-326, :334-346,
// :422-427), where OnBodyReady applies the identity's state: on, unless
// player_godmode switched it off for that identity during this mission.
// Godmode is the identity's, not a body's: a body that leaves its identity (a
// respawn's old body, missionserver.c:598-618; a finished logout, :677-711) is
// released first, so vanilla can still kill it (SetHealth at :608 and :744).
// While a body is in godmode, a periodic top-up keeps water and energy above the
// level where thirst and hunger take health (thirst.c:41-46, hunger.c:41-46).
// Only the player is switched, never its vehicle (the diag menu also switches
// the parent, plugindiagmenu.c:690-693).
class MCPGodmode
{
	// Seconds between two top-up passes over the players.
	static const float TOPUP_INTERVAL_S = 10.0;
	// The top-up keeps water and energy at least this far above
	// PlayerConstants.LOW_WATER_THRESHOLD and LOW_ENERGY_THRESHOLD, 300
	// (playerconstants.c:70, :72), at or under which thirst and hunger take
	// health: a floor of 500, under a new character's 600
	// (playerstatspco.c:297-298), so a fresh player is left as it is. The
	// fastest drain, sprinting, is 0.61 per second times 1 + value / 5000
	// (playerconstants.c:79-85, miscgameplayfunctions.c:767-788,
	// thirst.c:36-37), about 0.67 at the floor: one interval takes under 7 of
	// these 200.
	static const float TOPUP_MARGIN = 200.0;

	// Identity plain id -> true while switched off during this mission. An
	// identity missing from the map is on, the default.
	protected static ref map<string, bool> s_Off;
	protected static ref array<Man> s_Players;
	protected static float s_TopUpAccumS;

	// A mission starts with every identity on again (MissionServer.OnMissionStart).
	static void ResetMission()
	{
		s_Off = new map<string, bool>();
		s_Players = new array<Man>();
		s_TopUpAccumS = 0.0;
	}

	// The state query_all_players and query_player_state report: damage off on
	// this body (GetAllowDamage, object.c:1187).
	static bool IsOn(Man body)
	{
		if (!body)
		{
			return false;
		}
		return !body.GetAllowDamage();
	}

	// On unless player_godmode switched it off for this identity during this
	// mission.
	static bool IsOnFor(string plainId)
	{
		if (!s_Off)
		{
			return true;
		}
		if (s_Off.Contains(plainId))
		{
			return false;
		}
		return true;
	}

	// player_godmode: the identity's choice for the rest of the mission, applied
	// to the body it has now. Its next body gets it from OnBodyReady.
	static void Choose(PlayerBase player, string plainId, bool on)
	{
		Remember(plainId, on);
		Apply(player, on);
		Note(on, plainId, "player_godmode");
	}

	// MissionServer.InvokeOnConnect, after vanilla: a new or a loaded character,
	// or a respawn's new body. Without an identity there is no choice to look up,
	// so the default applies.
	static void OnBodyReady(PlayerBase player, PlayerIdentity identity)
	{
		bool on;
		string plainId;
		if (!player)
		{
			return;
		}
		on = true;
		plainId = "";
		if (identity)
		{
			plainId = identity.GetPlainId();
			on = IsOnFor(plainId);
		}
		Apply(player, on);
		Note(on, plainId, "connect");
	}

	// A body leaving its identity, before vanilla decides what becomes of it.
	// The identity's choice stays for its next body.
	static void ReleaseBody(PlayerBase player, string why)
	{
		string plainId;
		PlayerIdentity identity;
		if (!player)
		{
			return;
		}
		if (!IsOn(player))
		{
			return;
		}
		player.SetAllowDamage(true);
		plainId = "";
		identity = player.GetIdentity();
		if (identity)
		{
			plainId = identity.GetPlainId();
		}
		Print("[DayZ_MCP] godmode released uid=" + plainId + " reason=" + why);
	}

	// From MissionServer.OnUpdate. Every TOPUP_INTERVAL_S, one pass over the
	// players (game.c:947): only a living body in godmode is topped up.
	static void Tick(float timeslice)
	{
		int index;
		PlayerBase player;
		s_TopUpAccumS = s_TopUpAccumS + timeslice;
		if (s_TopUpAccumS < TOPUP_INTERVAL_S)
		{
			return;
		}
		s_TopUpAccumS = 0.0;
		if (!GetGame())
		{
			return;
		}
		if (!s_Players)
		{
			s_Players = new array<Man>();
		}
		s_Players.Clear();
		GetGame().GetPlayers(s_Players);
		index = 0;
		while (index < s_Players.Count())
		{
			player = PlayerBase.Cast(s_Players.Get(index));
			if (player && IsOn(player) && player.IsAlive())
			{
				TopUp(player);
			}
			index = index + 1;
		}
		s_Players.Clear();
	}

	// Raises water and energy to the floor, never lowers them (PlayerStat.Get
	// and Set, playerstatbase.c:85-116, :134-137).
	static void TopUp(PlayerBase player)
	{
		PlayerStat<float> waterStat;
		PlayerStat<float> energyStat;
		float waterFloor;
		float energyFloor;
		if (!player)
		{
			return;
		}
		waterFloor = PlayerConstants.LOW_WATER_THRESHOLD + TOPUP_MARGIN;
		energyFloor = PlayerConstants.LOW_ENERGY_THRESHOLD + TOPUP_MARGIN;
		waterStat = player.GetStatWater();
		if (waterStat && waterStat.Get() < waterFloor)
		{
			waterStat.Set(waterFloor);
		}
		energyStat = player.GetStatEnergy();
		if (energyStat && energyStat.Get() < energyFloor)
		{
			energyStat.Set(energyFloor);
		}
	}

	protected static void Remember(string plainId, bool on)
	{
		if (!s_Off)
		{
			s_Off = new map<string, bool>();
		}
		if (on)
		{
			s_Off.Remove(plainId);
			return;
		}
		s_Off.Set(plainId, true);
	}

	// The player's own damage switch, never its vehicle's. A body switched on is
	// topped up at once rather than at the next pass.
	protected static void Apply(PlayerBase player, bool on)
	{
		if (!player)
		{
			return;
		}
		player.SetAllowDamage(!on);
		if (on)
		{
			TopUp(player);
		}
	}

	// One script-log line per change.
	protected static void Note(bool on, string plainId, string why)
	{
		string line;
		line = "[DayZ_MCP] godmode on=0";
		if (on)
		{
			line = "[DayZ_MCP] godmode on=1";
		}
		Print(line + " uid=" + plainId + " reason=" + why);
	}
};

modded class PlayerBase
{
	// player_heal (inbox bef7), on the server: the full heal of the
	// Bullet_CupidsBolt branch of EEHitBy (playerbase.c:1302-1340), in its order.
	// Zones (health, shock and blood to their maximum, damagesystem.c:139-154),
	// modifiers, bleeding sources, stats (blood type, water, energy and the two
	// heat values kept), agents, stamina, then unconsciousness through the
	// vanilla juncture. Two differences from the template. MDF_IMMUNITYBOOST is
	// not activated: it is the vitamins' boost (vitaminbottle.c:13-18) for
	// VITAMINS_LIFETIME_SECS, 300 s (playerconstants.c:192), which a test would
	// then carry. Broken legs are cleared with SetBrokenLegs
	// (playerbase.c:3702-3724) when the modifier's deactivation did not
	// (brokenlegs.c:38-48). full also fills water and energy to their stats'
	// maximum, 5000 (playerstatspco.c:297-298), as ResetPlayer's set_max does
	// (playerbase.c:7625-7629). Vanilla allows damage before it raises health
	// and puts the state back after (pluginrepairing.c:36, :83, :88;
	// construction.c:82-84), so a godmode body is healed the same way and keeps
	// its godmode. Nothing here moves the player or touches its seat or vehicle.
	//
	// The splint, the heal's one change to the inventory, is vanilla's own: when
	// legs heal, BrokenLegsMdfr.OnDeactivate calls RemoveSplint if the player
	// IsWearingSplint (brokenlegs.c:38-44, playerbase.c:3914-3923), and
	// Deactivate runs it only for an active modifier (modifierbase.c:217-231).
	// RemoveSplint gives the Splint item back into the inventory, into the hands
	// when nothing else has room, or else on the ground half a metre in front of
	// the player, and deletes the applied one (miscgameplayfunctions.c:1636-1687,
	// humaninventory.c:65-71, playerbase.c:6480-6484). The heal reads those two
	// conditions before anything changes and returns where the Splint went:
	// "inventory" when the player holds one Splint more after ResetAll, "ground"
	// when it does not, "" when no splint came off. The applied splint's Delete
	// is deferred to the call queue (object.c:82-85), so it is not read after.
	string MCPHealServer(bool full)
	{
		bool damageWasAllowed;
		bool splintComesOff;
		int splintsBefore;
		string splintTo;
		int bloodType;
		float energyValue;
		float waterValue;
		float heatBuffer;
		float heatComfort;
		splintTo = "";
		splintComesOff = false;
		splintsBefore = 0;
		if (m_ModifiersManager && IsWearingSplint())
		{
			splintComesOff = m_ModifiersManager.IsModifierActive(eModifiers.MDF_BROKEN_LEGS);
		}
		if (splintComesOff)
		{
			splintsBefore = MCPCountSplints();
		}
		damageWasAllowed = GetAllowDamage();
		if (!damageWasAllowed)
		{
			SetAllowDamage(true);
		}
		DamageSystem.ResetAllZones(this);
		if (m_ModifiersManager)
		{
			m_ModifiersManager.ResetAll();
		}
		if (splintComesOff)
		{
			splintTo = "ground";
			if (MCPCountSplints() > splintsBefore)
			{
				splintTo = "inventory";
			}
		}
		if (GetBrokenLegs() != eBrokenLegs.NO_BROKEN_LEGS)
		{
			SetBrokenLegs(eBrokenLegs.NO_BROKEN_LEGS);
		}
		if (m_BleedingManagerServer)
		{
			m_BleedingManagerServer.RemoveAllSources();
		}
		if (GetPlayerStats())
		{
			bloodType = GetStatBloodType().Get();
			energyValue = GetStatEnergy().Get();
			waterValue = GetStatWater().Get();
			heatBuffer = GetStatHeatBuffer().Get();
			heatComfort = GetStatHeatComfort().Get();
			GetPlayerStats().ResetAllStats();
			if (full)
			{
				energyValue = GetStatEnergy().GetMax();
				waterValue = GetStatWater().GetMax();
			}
			GetStatBloodType().Set(bloodType);
			GetStatWater().Set(waterValue);
			GetStatEnergy().Set(energyValue);
			GetStatHeatBuffer().Set(heatBuffer);
			GetStatHeatComfort().Set(heatComfort);
		}
		if (m_AgentPool)
		{
			m_AgentPool.RemoveAllAgents();
		}
		if (m_StaminaHandler)
		{
			m_StaminaHandler.SetStamina(GameConstants.STAMINA_MAX);
		}
		if (IsUnconscious())
		{
			DayZPlayerSyncJunctures.SendPlayerUnconsciousness(this, false);
		}
		if (!damageWasAllowed)
		{
			SetAllowDamage(false);
		}
		return splintTo;
	}

	// The player's Splint items (splint.c:1-10; the applied one is a
	// Splint_Applied, splint.c:12) in its inventory and its hands
	// (EnumerateInventory, inventory.c:127; GetItemInHands,
	// playerbase.c:6437-6440). Reads only. The hands are counted on their own as
	// well: whether or not the enumeration already holds them, the count is
	// taken the same way before and after the heal.
	protected int MCPCountSplints()
	{
		array<EntityAI> items;
		int index;
		int count;
		count = 0;
		items = new array<EntityAI>();
		GetInventory().EnumerateInventory(InventoryTraversalType.PREORDER, items);
		index = 0;
		while (index < items.Count())
		{
			if (Splint.Cast(items.Get(index)))
			{
				count = count + 1;
			}
			index = index + 1;
		}
		if (Splint.Cast(GetItemInHands()))
		{
			count = count + 1;
		}
		return count;
	}
};
