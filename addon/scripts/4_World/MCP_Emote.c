// actor_emote (inbox 36fa): the one EmoteManager call the client bridge makes
// through a method of its own. end asks the server to end the playing emote
// with SendEmoteRequestSync(CALLBACK_CMD_END) (emotemanager.c:864), the request
// a client sends to end one, which CanPlayEmote always lets pass (:1029). The
// method sits inside the class, so CALLBACK_CMD_END resolves wherever vanilla
// declares it. 4_World, beside the other modded game classes; config.cpp
// compiles the whole folder.
modded class EmoteManager
{
	// False, and nothing sent, when CanPlayEmote or CanPlayEmoteClientCheck
	// (:1026, :1098) refuses the end: the check SendEmoteRequestSync itself
	// makes before it sends (:899).
	bool MCP_RequestEmoteEnd()
	{
		if (!CanPlayEmote(CALLBACK_CMD_END) || !CanPlayEmoteClientCheck(CALLBACK_CMD_END))
		{
			return false;
		}
		SendEmoteRequestSync(CALLBACK_CMD_END);
		return true;
	}
};
