modded class MissionGameplay
{
	void ~MissionGameplay()
	{
		MCPClientBridge.ShutdownInstance();
	}

	override void OnMissionStart()
	{
		super.OnMissionStart();

		MCPClientBridge bridge = MCPClientBridge.Get();
		if (bridge)
		{
			bridge.OnTick(0.0);
			bridge.ReleaseGameFocus();
		}
	}

	override void OnUpdate(float timeslice)
	{
		// The HUD cursor updates inside super. Stamp the frame first so that
		// update and a later action_cursor read share one tick.
		MCPFrame.Advance();
		super.OnUpdate(timeslice);

		MCPClientBridge bridge = MCPClientBridge.Get();
		if (bridge)
		{
			bridge.OnTick(timeslice);
		}
	}

	override void OnKeyPress(int key)
	{
		super.OnKeyPress(key);

		MCPClientBridge bridge = MCPClientBridge.Get();
		if (bridge)
		{
			bridge.OnMissionKeyPress(key);
		}
	}
};
