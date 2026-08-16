modded class MissionServer
{
	void ~MissionServer()
	{
		MCPBridge.ShutdownInstance();
	}

	override void OnMissionStart()
	{
		super.OnMissionStart();

		MCPBridge bridge = MCPBridge.Get();
		if (bridge)
		{
			bridge.OnTick(0.0);
		}
	}

	override void OnUpdate(float timeslice)
	{
		super.OnUpdate(timeslice);

		MCPBridge bridge = MCPBridge.Get();
		if (bridge)
		{
			bridge.OnTick(timeslice);
		}
	}
};
