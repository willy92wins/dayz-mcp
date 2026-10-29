modded class MissionServer
{
	void ~MissionServer()
	{
		MCPBridge.ShutdownInstance();
	}

	override void OnMissionStart()
	{
		super.OnMissionStart();

		// Godmode choices last one mission (MCPGodmode, MCP_PlayerCare.c).
		// B1: the default is on only when the MCP config on disk is the
		// harness's profile=test one; no config, or any other profile,
		// leaves players mortal and without the water/energy refill.
		MCPGodmode.ResetMission();
		MCPGodmode.SetDefaultOn(MCPBridge.ConfigIsTestProfile());

		MCPBridge bridge = MCPBridge.Get();
		if (bridge)
		{
			bridge.OnTick(0.0);
		}
	}

	override void OnUpdate(float timeslice)
	{
		super.OnUpdate(timeslice);

		// The godmode top-up runs whether or not the bridge is configured:
		// the mission default was fixed at OnMissionStart from the config
		// on disk, and Tick leaves at once when no body is in godmode.
		MCPGodmode.Tick(timeslice);

		MCPBridge bridge = MCPBridge.Get();
		if (bridge)
		{
			bridge.OnTick(timeslice);
		}
	}

	// missionserver.c:422-427. Vanilla calls it for a new character
	// (ClientNewEvent, :316-326, which is also how a respawn gets its new body)
	// and for a loaded one (ClientReadyEvent, :334-346). After vanilla, the body
	// gets its identity's godmode: its explicit choice this mission, else the
	// mission default (on only under profile=test).
	override void InvokeOnConnect(PlayerBase player, PlayerIdentity identity)
	{
		super.InvokeOnConnect(player, identity);
		MCPGodmode.OnBodyReady(player, identity);
	}

	// missionserver.c:598-618: the body that respawns, which vanilla kills here
	// when it is unconscious or restrained (:602-609); the new body comes later
	// through InvokeOnConnect. Godmode leaves this body first so that kill works.
	override void OnClientRespawnEvent(PlayerIdentity identity, PlayerBase player)
	{
		MCPGodmode.ReleaseBody(player, "respawn");
		super.OnClientRespawnEvent(identity, player);
	}

	// missionserver.c:429-434, called when a logout ends (:690), before
	// HandleBody may kill the body that stays (:705, :735-751). Godmode leaves
	// that body first so that kill works.
	override void InvokeOnDisconnect(PlayerBase player)
	{
		MCPGodmode.ReleaseBody(player, "disconnect");
		super.InvokeOnDisconnect(player);
	}
};
