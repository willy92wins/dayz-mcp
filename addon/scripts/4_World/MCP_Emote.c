// actor_emote (inbox 36fa): the one EmoteManager call the client bridge makes
// through a method of its own. end asks the server to end the playing emote
// with SendEmoteRequestSync(CALLBACK_CMD_END) (1.30 emotemanager.c:864), the
// request vanilla's key and gestures menu send to end one
// (1.30 emotemanager.c:420-423), which CanPlayEmote always lets pass
// (1.30 :1029). The method sits inside the class, so CALLBACK_CMD_END resolves
// wherever vanilla declares it. 4_World, beside the other modded game classes;
// config.cpp compiles the whole folder.
modded class EmoteManager
{
	// False, and nothing sent, when CanPlayEmote or CanPlayEmoteClientCheck
	// (1.30 emotemanager.c:1026, :1098) refuses the end. Those are only two of
	// the checks SendEmoteRequestSync makes before it sends
	// (1.30 emotemanager.c:899): it also needs ctx.CanStoreInputUserData() and
	// canProceed, the playing emote's CanBeCanceledNormally(m_Callback)
	// (1.30 :892-897), which this method does not ask. When one of those stops
	// it, nothing is sent, this still returns true, the emote plays on and the
	// end job answers emote_timed_out. CanBeCanceledNormally is true in
	// EmoteBase (1.30 emotebase.c:17-20) and only EmoteSuicide overrides it
	// (1.30 emoteclasses.c:711-718), so canProceed only stops the end of a
	// suicide emote the player started from the menu (it is not in
	// actor_emote's catalog).
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
