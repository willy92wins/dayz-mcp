class CfgPatches
{
	class DayZ_MCP
	{
		units[] = {};
		weapons[] = {};
		requiredVersion = 0.1;
		requiredAddons[] = {"DZ_Data"};
	};
};

class CfgMods
{
	class DayZ_MCP
	{
		dir = "DayZ_MCP";
		name = "DayZ_MCP";
		type = "mod";
		hideName = 1;
		hidePicture = 1;
		dependencies[] = {"World", "Mission"};
		class defs
		{
			class worldScriptModule
			{
				value = "";
				files[] = {"DayZ_MCP/scripts/4_World"};
			};
			class missionScriptModule
			{
				value = "";
				files[] = {"DayZ_MCP/scripts/5_Mission"};
			};
		};
	};
};
