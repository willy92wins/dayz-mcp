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
// #292: also supply PluginItemDiagnostic's debug-profile dependency. In
// P:/scripts/4_world/plugins/pluginmanager.c:77, :89 the consumer is Diag,
// the profile Debug; pluginbase/pluginitemdiagnostic.c:92-97 fetches it in
// OnInit; a missing plugin logs a stack (pluginmanager.c:332-341).
// PluginsInit spawns AND stores all instances before any OnInit
// (pluginmanager.c:117-135), so appending the profile here is safe.
// Under P:/scripts/4_world/plugins/pluginbase/pluginfilehandler/,
// pluginconfighandler.c:5-26 loads the config in the constructor;
// pluginconfighandler/pluginconfigdebugprofile.c:328-367 builds defaults,
// :1076-1174 only loads presets, with no other plugin instance dependency
// (PluginConfigDebugProfileFixed references are constants, :1843-1845).
// Its getters persist missing defaults (:138-167, :270-302): normal profile
// preferences, not gameplay changes. :1385-1395 reads legacy
// $profile:debugProfile.cfg if ScriptConsole/ is absent; SaveFile creates
// that folder before writing debugProfile.cfg (pluginfilehandler.c:82-109;
// P:/scripts/3_game/constants.c:250, :257). Keep the diagnostic plugin alive.
modded class PluginManager
{
	// After super, so vanilla's list keeps its order. An internal build
	// (IsDebug) has registered both already and must not get second copies.
	override void Init()
	{
		super.Init();
		if (!g_Game.IsDebug())
		{
			RegisterPlugin("PluginInventoryDebug", true, true, true);
			RegisterPlugin("PluginConfigDebugProfile", true, true, true);
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
