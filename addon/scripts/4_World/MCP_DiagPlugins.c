#ifdef DIAG_DEVELOPER
// DayZDiag only (fb-20260927-213700-c4e1). PluginInventoryDebug exists only
// under DIAG_DEVELOPER (plugininventorydebug.c:4-55), and DayZPlayerInventory
// reads it with no null check under the same define (dayzplayerinventory.c:838,
// :2719 and 12 more sites). PluginManager.Init registers it through
// RegisterPluginDebug (pluginmanager.c:98-100), which passes
// reg_on_release=false (:240-243), so RegisterPlugin returns before the insert
// unless IsDebug() (:213-218). DayZDiag defines DIAG_DEVELOPER but IsDebug() is
// false there, and those reads throw NULL pointer: in game, a JUNCTURE hand
// event (dayzplayerinventory.c:2707-2727) aborted that frame's CommandHandler.
// The plugin's defaults (plugininventorydebug.c:17-22) keep every site on the
// retail branch: desync repair on, local-only moves off.
modded class PluginManager
{
	// After super, so vanilla's list keeps its order. An internal build
	// (IsDebug) has registered it already and must not get a second copy.
	override void Init()
	{
		super.Init();
		if (!g_Game.IsDebug())
		{
			RegisterPlugin("PluginInventoryDebug", true, true, true);
		}
	}

	// super spawns every registered type (pluginmanager.c:109-137). One line
	// per manager start, on each peer: the plugin exists or it does not.
	override void PluginsInit()
	{
		super.PluginsInit();
		if (GetPluginByType(PluginInventoryDebug))
		{
			Print("[DayZ_MCP] diag PluginInventoryDebug registered=1");
			return;
		}
		Print("[DayZ_MCP] diag PluginInventoryDebug registered=0");
	}
}
#endif
