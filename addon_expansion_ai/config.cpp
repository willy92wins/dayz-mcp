class CfgPatches
{
	class DayZ_MCP_ExpansionAI
	{
		units[] = {};
		weapons[] = {};
		requiredVersion = 0.1;
		requiredAddons[] = {"DayZ_MCP", "DayZExpansion_AI_Scripts"};
	};
};

class CfgMods
{
	class DayZ_MCP_ExpansionAI
	{
		dir = "DayZ_MCP_ExpansionAI";
		name = "DayZ_MCP_ExpansionAI";
		type = "mod";
		hideName = 1;
		hidePicture = 1;
		dependencies[] = {"Mission"};
		class defs
		{
			class missionScriptModule
			{
				value = "";
				files[] = {"DayZ_MCP_ExpansionAI/scripts/5_Mission"};
			};
		};
	};
};
