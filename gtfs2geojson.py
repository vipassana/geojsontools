#!/usr/bin/env python
"""
gtfs2geojson
Converts GTFS data into GeoJSON format
Copyright 2014-2015 Michael Farrell <http://micolous.id.au>
Copyright 2019 Vipassana Vijayarangan


License: 3-clause BSD, see COPYING
"""

import geojson, argparse, csv, zipfile, simplejson
try:
	from cStringIO import StringIO
except ImportError:
	from StringIO import StringIO
from datetime import timedelta
from decimal import Decimal

class DecimalEncoder(geojson.GeoJSONEncoder):
    def _iterencode(self, o, markers=None):
        if isinstance(o, decimal.Decimal):
            return float(o)
        return super(DecimalEncoder, self)._iterencode(o, markers)

def gtfs_stops(gtfs, output_f):
	"""
	For each stop, convert it into a GeoJSON Point, and make all of it's attributes available.

	:param gtfs file: Input GTFS ZIP.
	:param output_f file: Output GeoJSON file stream.
	"""
	#TODO
	stops_file = [x for x in gtfs.namelist() if 'stops' in x][0]

	stops_c = csv.reader(swallow_windows_unicode(gtfs.open(stops_file, 'r')))

	output_layer = geojson.FeatureCollection([])
	# assume WGS84 CRS
	output_layer.crs = geojson.crs.Named('urn:ogc:def:crs:OGC:1.3:CRS84')

	header = stops_c.next()
	lat_col = header.index('stop_lat')
	lng_col = header.index('stop_lon')
	id_col = header.index('stop_id')

	for row in stops_c:
		lat, lng = Decimal(row[lat_col]), Decimal(row[lng_col])

		# make dict of other properties
		props = dict()
		for i, h in enumerate(header):
			if h in ('stop_lat', 'stop_lon'):
				continue

			if row[i] != '':
				props[h] = row[i]

		output_layer.features.append(geojson.Feature(
			geometry=geojson.Point(
				coordinates=(lng, lat)
			),
			properties=props,
			id=row[id_col]
		))

	geojson.dump(output_layer, output_f)

def time_as_timedelta(time):
	# We need to be able to handle values above 24:00:00, as they mean "tomorrow".
	try:
		h, m, s = (int(x) for x in time.split(':'))
	except ValueError:
		return None
	return timedelta(hours=h, minutes=m, seconds=s)

def gtfs_routes(gtfs, output_f):
	"""
	For each route, convert it's 'shape' into a GeoJSON LineString, and make all
	of it's attributes available.

	:param gtfs file: Input GTFS ZIP
	:param output_f file: Output GeoJSON file stream.

	"""

	# Load up the stop times so we can find which are the best routes.
	#TODO
	stop_times_file = [x for x in gtfs.namelist() if 'stop_times' in x][0]

	stoptimes_c = csv.reader((gtfs.open(stop_times_file, 'r')))
	header = stoptimes_c.next()
	trip_id_col = header.index('trip_id')
	arrtime_col = header.index('arrival_time')
	deptime_col = header.index('departure_time')
	stopseq_col = header.index('stop_sequence')
	trip_times = {}
	for row in stoptimes_c:
		if row[trip_id_col] not in trip_times:
			# earliest seq, latest seq, earliest seq dep time, latest seq dep time
			trip_times[row[trip_id_col]] = [None, None, None, None]

		arrtime = time_as_timedelta(row[arrtime_col])
		deptime = time_as_timedelta(row[deptime_col])
		if arrtime is None or deptime is None:
			# bad data, skip!
			continue
		seq = int(row[stopseq_col])

		# Find if this is an earlier item in the sequence
		if trip_times[row[trip_id_col]][0] is None or trip_times[row[trip_id_col]][0] > seq:
			trip_times[row[trip_id_col]][0] = seq
			trip_times[row[trip_id_col]][2] = deptime

		# Find if this is an later item in the sequence
		if trip_times[row[trip_id_col]][1] is None or trip_times[row[trip_id_col]][1] < seq:
			trip_times[row[trip_id_col]][1] = seq
			trip_times[row[trip_id_col]][3] = arrtime

	# Load the shapes into a map that we can lookup.
	# We should do all the geometry processing here so that we only have to do
	# this once-off.
	#TODO
	shapes_file = [x for x in gtfs.namelist() if 'shapes' in x][0]
	shapes_c = csv.reader(swallow_windows_unicode(gtfs.open(shapes_file, 'r')))

	header = shapes_c.next()
	shape_id_col = header.index('shape_id')
	shape_lat_col = header.index('shape_pt_lat')
	shape_lng_col = header.index('shape_pt_lon')
	shape_seq_col = header.index('shape_pt_sequence')
	shape_dist_col = header.index('shape_dist_traveled') if 'shape_dist_traveled' in header else None

	shapes = {}
	shape_lengths = {}
	for row in shapes_c:
		if row[shape_id_col] not in shapes:
			shapes[row[shape_id_col]] = {}

		shapes[row[shape_id_col]][int(row[shape_seq_col])] = (Decimal(row[shape_lng_col]), Decimal(row[shape_lat_col]))

		# Calculate length according to GTFS
		# This could also be calculated by the geometry, but we trust GTFS, right...
		if shape_dist_col is not None and row[shape_dist_col]:
			length = Decimal(row[shape_dist_col])
			if row[shape_id_col] not in shape_lengths or shape_lengths[row[shape_id_col]] < length:
				shape_lengths[row[shape_id_col]] = length

	# translate the shapes into a LineString for use by the GeoJSON module
	for shape_id in shapes.iterkeys():
		shape_keys = shapes[shape_id].keys()
		shape_keys.sort()
		shape = []
		for ordinal in shape_keys:
			shape.append(shapes[shape_id][ordinal])

		shapes[shape_id] = shape

	# Make a matching dict between routes and shapes
	trips = {}
	trips_ref = {}
	route_time = {}

	#TODO
	trips_file = [x for x in gtfs.namelist() if 'trips' in x][0]

	trips_c = csv.reader(swallow_windows_unicode(gtfs.open(trips_file, 'r')))
	header = trips_c.next()
	route_id_col = header.index('route_id')
	shape_id_col = header.index('shape_id')
	trip_id_col = header.index('trip_id')
	for row in trips_c:
		# reference count the shapes
		if row[route_id_col] not in trips_ref:
			# route is unknown, create dict
			trips_ref[row[route_id_col]] = {}
			route_time[row[route_id_col]] = trip_times[row[trip_id_col]]

		if row[shape_id_col] not in trips_ref[row[route_id_col]]:
			# shape is unknown, create counter
			trips_ref[row[route_id_col]][row[shape_id_col]] = 0

		# increment counter
		trips_ref[row[route_id_col]][row[shape_id_col]] += 1

	# now we're done, iterate through the reference-counters and find the best
	# shape
	for route_id, candidate_shapes in trips_ref.iteritems():
		popular_shape, popular_shape_refs = None, 0
		for shape_id, refs in candidate_shapes.iteritems():
			if refs > popular_shape_refs:
				popular_shape, popular_shape_refs = shape_id, refs

		# now we should have the route's shape
		assert popular_shape is not None, 'Couldn\'t find a shape for route %r' % route_id
		trips[route_id] = popular_shape

	# Cleanup unused variables
	del trip_times

	# lets setup our output file
	output_layer = geojson.FeatureCollection([])
	# assume WGS84 CRS
	output_layer.crs = geojson.crs.Named('urn:ogc:def:crs:OGC:1.3:CRS84')

	# now we have all the shapes available, translate the routes
	#TODO
	routes_file = [x for x in gtfs.namelist() if 'routes' in x][0]

	routes_c = csv.reader(swallow_windows_unicode(gtfs.open(routes_file, 'r')))
	header = routes_c.next()
	route_id_col = header.index('route_id')

	for row in routes_c:
		# make dict of other properties
		props = dict()
		for i, h in enumerate(header):
			if row[i] != '':
				props[h] = row[i]

		if row[route_id_col] not in trips:
			# Route has no trips!
			print "Warning: route has no trips, skipping: %r" % (row,)
			continue

		props['shape_id'] = trips[row[route_id_col]]
		props['shape_refs'] = trips_ref[row[route_id_col]][props['shape_id']]
		if shape_dist_col is not None and len(shape_lengths) > 0:
			props['shape_length'] = shape_lengths[props['shape_id']]
		props['duration_sec'] = (route_time[row[route_id_col]][3] - route_time[row[route_id_col]][2]).total_seconds()

		output_layer.features.append(geojson.Feature(
			geometry=geojson.LineString(
				coordinates=shapes[trips[row[route_id_col]]]
			),
			properties=props,
			id=row[route_id_col]
		))

	# now flush the GeoJSON layer to a file.
	geojson.dump(output_layer, output_f, cls=DecimalEncoder)


def swallow_windows_unicode(fileobj, rewind=True):
	"""
	Windows programs (specifically, Notepad) puts '\xef\xbb\xbf' at the start of
	a Unicode text file.  This is used to handle "utf-8-sig" files.

	This function looks for those bytes and advances the stream past them if
	they are present.

	Returns fileobj, fast-forwarded past the characters.
	"""
	if rewind:
		try:
			pos = fileobj.tell()
		except:
			pos = None

	try:
		bom = fileobj.read(3)
	except:
		# End of file, revert!
		fileobj.seek(pos)
	if bom == '\xef\xbb\xbf':
		return fileobj

	# Bytes not present, rewind the stream
	if rewind:
		if pos is None:
			# .tell is not supported, dump the file contents into a cStringID
			fileobj = StringIO(bom + fileobj.read())
		else:
			fileobj.seek(pos)
	return fileobj


def main():
	parser = argparse.ArgumentParser()

	parser.add_argument('-o', '--output',
		required=True,
		type=argparse.FileType('wb'),
		help='Output GeoJSON file'
	)

	parser.add_argument('input_gtfs',
		type=argparse.FileType('rb'),
		help='Path to GTFS ZIP file to extract data from.')

	group = parser.add_mutually_exclusive_group(required=True)

	group.add_argument('-r', '--routes',
		action='store_true',
		help='Route conversion mode')

	group.add_argument('-s', '--stops',
		action='store_true',
		help='Stop conversion mode')

	options = parser.parse_args()

	assert options.routes or options.stops

	# Open ZIP
	gtfs = zipfile.ZipFile(options.input_gtfs, 'r')
	print
	if options.routes:
		gtfs_routes(gtfs, options.output)
	elif options.stops:
		gtfs_stops(gtfs, options.output)

if __name__ == '__main__':
	main()
