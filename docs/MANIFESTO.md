# Los Cumbres For The People

I work at Rubin Observatory.

When the Legacy Survey of Space And Time is running at its full capacity, it will generate about seven million alerts a night.

There are about a thousand professional observatories in the world.

Some of them have some budget to chase targets of opportunity, but we can confidently expect that effectively all of them will be chasing the most interesting alerts on any given night.

That's going to cover a few dozen, maybe a few hundred, objects.

Sure, the vast majority of alerts will be either too faint or in the wrong place for any given amateur observer to see.

However, at seven million a night, there are going to be quite a few that are both bright and visible to amateur observers on any given night.

A low-end robotic telescope, like a SeeStar S50, is on the order of $500 in 2026.
That is not a huge investment.

There are also plenty of amateurs with, say, 30-70cm telescopes and fairly sophisticated domes, and there are even some with 1m-or-bigger mirrors.

What I am proposing is a matchmaking system.

If you have an observatory you don't mind letting the Los Cumbres For The People's servers drive after you've done the observing and photography you wanted and have gone to bed, then we can pair alerts with sites able to observe the location of the alert.
The observers can upload their data (where is very much to be determined).
Much of it will not be helpful.
Most of the rest will not be used by a professional astronomer to write a paper.
However, at least some fraction of these observations are likely to support somebody's research, or help establish asteroid tracklets, or provide optical confirmation of gravitational-wave events, or *something*.

The actual broker system shouldn't be that hard: there will be a server that an observatory can connect to and send an identification packet, which would look something like "Hi, I'm Observatory XYZ, my lat/long/elevation are ..., I can observe things down to 20th magnitude with an exposure of 30 minutes at 650nm, and I will be available tonight from 0630 UTC to 1230 UTC."

Then the server will take that data and match it against the list of alerts it's handling from one or more of the public alert brokers.
It will find an alert that it hasn't already sent for followup that should be observable by the given site, and send pointing instructions (ra, dec, exposure time).

The site receives that, and takes an image given those specifications.
Once done, it submits the data it recorded (Zooniverse is the obvious upstream clearinghouse, but I have not contacted them yet; I want to get a not-entirely-broken proof of concept working before asking them to commit anything).

The vast majority of the work here is just ingesting the alert stream from a broker; a simple flux cutoff to discard anything fainter than some limit will be easy to engineer such that the server has more objects than can feasibly be observed in a night, but not so many as to require an enormous amount of memory to store and traverse the stream.

Matching the capabilitites of an observatory against an alert will be easy: sky visibility is already part of astroplan, and adding brightness thresholds is straightforward.

On the observatory end, all that's necessary is (fairly minimal) network connectivity and an interface to drive the telescope given ra and dec.
Much as I loathe Elon Musk, Starlink (and its eventual competitors) does provide the capability for even very remote sites to have adequate network access.
A Raspberry Pi ($75 in 2026) is more than adequate for this job; SeeStar already has a perfectly decent Python API.
For larger and less-off-the-shelf telescopes some work may be necessary to write these drivers, but if it's an automatically-controlled scope at all, probably at worst the Pi would have to pretend to be a HID (which is to say, a keyboard), typing commands into the telescope.

Fundamentally, it's crowdsourcing Rubin alert followup.
Conceptually it's somewhere in the same space as Seti@Home and BHTOM.
