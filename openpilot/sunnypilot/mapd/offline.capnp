@0xda3a0d9284ca402f;

# Schema for the offline map data downloaded by mapd (the OSM page in settings).
#
# Vendored from pfeiferj/mapd (MIT), cereal/offline/offline.capnp, with the Go
# package annotations removed so pycapnp can load it without /go.capnp. The struct
# id, field numbering and ordering are unchanged, which is what the wire format
# depends on -- do not renumber anything here.
#
# Files live at {Paths.mapd_root()}/offline/<groupLat>/<groupLon>/<minLat>_<minLon>_<maxLat>_<maxLon>
# in 0.25 degree cells, grouped into 2 degree directories. See offline_map.py.

enum HighwayClass {
  unknown @0;
  motorway @1;
  motorwayLink @2;
  trunk @3;
  trunkLink @4;
  primary @5;
  primaryLink @6;
  secondary @7;
  secondaryLink @8;
  tertiary @9;
  tertiaryLink @10;
  unclassified @11;
  residential @12;
  livingStreet @13;
}

struct Way {
  name @0 :Text;
  ref @1 :Text;
  maxSpeed @2 :Float64;
  minLat @3 :Float64;
  minLon @4 :Float64;
  maxLat @5 :Float64;
  maxLon @6 :Float64;
  nodes @7 :List(Coordinates);
  lanes @8 :UInt8;
  advisorySpeed @9 :Float64;
  hazard @10 :Text;
  oneWay @11 :Bool;
  maxSpeedForward @12 :Float64;
  maxSpeedBackward @13 :Float64;
  id @14 :Int64;
  highwayClass @15 :HighwayClass;
  maxSpeedConditional @16 :Text;
  maxSpeedForwardConditional @17 :Text;
  maxSpeedBackwardConditional @18 :Text;
}

struct Coordinates {
  latitude @0 :Float64;
  longitude @1 :Float64;
}

struct Offline {
  minLat @0 :Float64;
  minLon @1 :Float64;
  maxLat @2 :Float64;
  maxLon @3 :Float64;
  ways @4 :List(Way);
  overlap @5 :Float64;
}
