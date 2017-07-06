from __future__ import absolute_import, unicode_literals

import re
import json
import base64
import logging
import collections

import synapse.common as s_common
import synapse.compat as s_compat
import synapse.dyndeps as s_dyndeps

import synapse.lib.time as s_time
import synapse.lib.syntax as s_syntax
import synapse.lib.modules as s_modules

import synapse.lookup.iso3166 as s_l_iso3166

logger = logging.getLogger(__name__)

guidre = re.compile('^[0-9a-f]{32}$')
def isguid(text):
    return guidre.match(text) is not None

class DataType:

    subprops = ()

    def __init__(self, tlib, name, **info):
        self.tlib = tlib
        self.name = name
        self.info = info
        s_common.reqStorDict(info)

    def _raiseBadValu(self, valu, **info):
        raise s_common.BadTypeValu(name=self.name, valu=valu, **info)

    def get(self, prop, defval=None):
        '''
        Retrieve a type info property from this type or parent types.

        Example:

            ex = item.get('doc')

        '''
        return self.tlib.getTypeInfo(self.name, prop, defval=defval)

    def subs(self):
        '''
        Implement if the presence of a property with this type requires sub props.
        '''
        return self.subprops

    def extend(self, name, **info):
        '''
        Construct a new subtype from this instance.
        '''
        for k, v in self.info.items():
            info.setdefault(k, v)

        return self.__class__(self.tlib, name, **info)

    def parse(self, text, oldval=None):
        '''
        Parse input text and return the system mode (normalized) value for the type.

        Example:

            valu = tobj.parse(text)

        '''
        return self.norm(text, oldval=oldval)

    def repr(self, valu):
        return valu

class GuidType(DataType):

    def __init__(self, tlib, name, **info):
        DataType.__init__(self, tlib, name, **info)
        self._guid_alias = info.get('alias')
        # TODO figure out what to do about tlib vs core issues
        self._getTufoByProp = getattr(tlib, 'getTufoByProp', None)

    def norm(self, valu, oldval=None):

        if not s_compat.isstr(valu) or len(valu) < 1:
            self._raiseBadValu(valu)

        # generate me one.  we dont care.
        if valu == '*':
            return s_common.guid(), {}

        if valu[0] != '$':
            retn = valu.lower().replace('-', '')
            if not isguid(retn):
                self._raiseBadValu(valu)

            return retn, {}

        if self._guid_alias is None:
            self._raiseBadValu(valu, mesg='guid resolver syntax used with non-aliased guid')

        if self._getTufoByProp is None:
            self._raiseBadValu(valu, mesg='guid resolver syntax used with non-cortex tlib')

        # ( sigh... eventually everything will be a cortex... )
        node = self._getTufoByProp(self._guid_alias, valu[1:])
        if node is None:
            self._raiseBadValu(valu, mesg='no result for guid resolver')

        iden = node[1].get(node[1].get('tufo:form'))
        return iden, {}

class StrType(DataType):

    def __init__(self, tlib, name, **info):
        DataType.__init__(self, tlib, name, **info)

        self.regex = None
        self.envals = None
        self.restrip = None
        self.frobintfmt = None

        self.strip = info.get('strip', 0)
        self.nullval = info.get('nullval')

        enumstr = info.get('enums')
        if enumstr is not None:
            self.envals = enumstr.split(',')

        regex = info.get('regex')
        if regex is not None:
            self.regex = re.compile(regex)

        restrip = info.get('restrip')
        if restrip is not None:
            self.restrip = re.compile(restrip)

        frobintfmt = info.get('frob_int_fmt')
        if frobintfmt is not None:
            self.frobintfmt = frobintfmt

    def norm(self, valu, oldval=None):

        if self.frobintfmt and s_compat.isint(valu):
            valu = self.frobintfmt % valu

        if not s_compat.isstr(valu):
            self._raiseBadValu(valu)

        if self.info.get('lower'):
            valu = valu.lower()

        if valu == self.nullval:
            return valu, {}

        if self.restrip:
            valu = self.restrip.sub('', valu)

        if self.strip:
            valu = valu.strip()

        if self.envals is not None and valu not in self.envals:
            self._raiseBadValu(valu, enums=self.info.get('enums'))

        if self.regex is not None and not self.regex.match(valu):
            self._raiseBadValu(valu, regex=self.info.get('regex'))

        return valu, {}

class JsonType(DataType):

    def norm(self, valu, oldval=None):

        if not s_compat.isstr(valu):
            return json.dumps(valu, separators=(',', ':')), {}

        try:
            return json.dumps(json.loads(valu), separators=(',', ':')), {}
        except Exception as e:
            self._raiseBadValu(valu)

class IntType(DataType):

    def __init__(self, tlib, name, **info):
        DataType.__init__(self, tlib, name, **info)

        self.fmt = info.get('fmt', '%d')
        #self.modval = info.get('mod',None)
        self.minval = info.get('min', None)
        self.maxval = info.get('max', None)

        self.ismin = info.get('ismin', False)
        self.ismax = info.get('ismax', False)

        # cache the min or max function to avoid cond logic
        # during norm() for perf
        self.minmax = None

        if self.ismin:
            self.minmax = min

        elif self.ismax:
            self.minmax = max

    def repr(self, valu):
        return self.fmt % valu

    def norm(self, valu, oldval=None):

        if s_compat.isstr(valu):
            try:
                valu = int(valu, 0)
            except ValueError as e:
                self._raiseBadValu(valu)

        if not s_compat.isint(valu):
            self._raiseBadValu(valu)

        if oldval is not None and self.minmax:
            valu = self.minmax(valu, oldval)

        if self.minval is not None and valu < self.minval:
            self._raiseBadValu(valu, minval=self.minval)

        if self.maxval is not None and valu > self.maxval:
            self._raiseBadValu(valu, maxval=self.maxval)

        return valu, {}

def enMsgB64(item):
    # FIXME find a way to go directly from binary bytes to
    # base64 *string* to avoid the extra decode pass..
    return base64.b64encode(s_common.msgenpack(item)).decode('utf8')

def deMsgB64(text):
    # FIXME see above
    return s_common.msgunpack(base64.b64decode(text.encode('utf8')))

jsseps = (',', ':')

def islist(x):
    return type(x) in (list, tuple)

class MultiFieldType(DataType):

    def __init__(self, tlib, name, **info):
        DataType.__init__(self, tlib, name, **info)
        self.fields = None

    def _norm_fields(self, valu):

        fields = self._get_fields()

        if len(valu) != len(fields):
            self._raiseBadValu(valu, mesg='field count != %d' % (len(fields),))

        vals = []
        subs = {}

        for valu, (name, item) in s_compat.iterzip(valu, fields):

            norm, fubs = item.norm(valu)

            vals.append(norm)

            subs[name] = norm
            for fubk, fubv in fubs.items():
                subs[name + ':' + fubk] = fubv

        return vals, subs

    def _get_fields(self):

        if self.fields is None:

            self.fields = []

            # maintain legacy "fields=" syntax for a bit yet...
            fields = self.info.get('fields')
            if fields is not None:
                if fields:
                    for part in fields.split('|'):
                        fname, ftype = part.split(',')
                        fitem = self.tlib.getTypeInst(ftype)
                        self.fields.append((fname, fitem))

                return self.fields

            # process names= and types= info fields
            fnames = []
            ftypes = []

            fnstr = self.info.get('names')
            if fnstr:
                fnames.extend(fnstr.split(','))

            ftstr = self.info.get('types', '')
            if ftstr:
                ftypes.extend(ftstr.split(','))

            self.flen = len(ftypes)

            if len(fnames) != self.flen:
                raise s_common.BadInfoValu(name='types', valu=ftstr, mesg='len(names) != len(types)')

            for i in range(self.flen):
                item = self.tlib.getTypeInst(ftypes[i])
                self.fields.append((fnames[i], item))

        return self.fields

def _splitpairs(text, sep0, sep1):
    '''
    Split parts via sep0 and then pairs by sep2
    '''
    for part in text.split(sep0):
        k,v = part.split(sep1)
        yield k.strip(),v.strip()

class CompType(DataType):

    def __init__(self, tlib, name, **info):
        DataType.__init__(self, tlib, name, **info)

        self.fields = []
        self.optfields = []

        fstr = self.info.get('fields')
        if fstr:

            if fstr.find('=') != -1:
                self.fields.extend( _splitpairs(fstr, ',', '='))

            else:
                self.fields.extend( _splitpairs(fstr, '|', ','))

        self.fsize = len(self.fields)

        ostr = self.info.get('optfields')
        if ostr:
            self.optfields.extend( _splitpairs(ostr, ',', '='))
            # stabilize order to alphabetical since it effects
            # the eventual guid generation
            self.optfields.sort()

    def _norm_str(self, text, oldval=None):

        text = text.strip()

        if not text:
            self._raiseBadValu(text)

        if text[0] != '(':
            return self.tlib.getTypeNorm('guid', text)

        vals, off = s_syntax.parse_list(text)
        if off != len(text):
            self._raiseBadValu(text)

        return self._norm_list(vals)

    def _norm_list(self, valu, oldval=None):

        opts = {}
        subs = {}
        retn = []

        vlen = len(valu)

        if vlen < self.fsize:
            self._raiseBadValu(valu, mesg='Expected %d fields and got %d' % (self.fsize,len(valu)))

        for k,v in valu[self.fsize:]:
            opts[k] = v

        vals = valu[:self.fsize]
        for v,(name,tname) in s_compat.iterzip(vals,self.fields):

            norm,ssubs = self.tlib.getTypeNorm(tname,v)

            subs[name] = norm
            for subkey,subval in ssubs.items():
                subs[name + ':' + subkey] = subval
            retn.append(norm)

        for name,tname in self.optfields:

            v = opts.get(name)
            if v is None:
                continue

            norm,ssubs = self.tlib.getTypeNorm(tname,v)

            subs[name] = norm
            for subkey,subval in ssubs.items():
                subs[name + ':' + subkey] = subval

            retn.append( (name,norm) )

        return s_common.guid(retn), subs

    def norm(self, valu, oldval=None):

        # if it's already a guid, we have nothing to normalize...
        if s_compat.isstr(valu):
            return self._norm_str(valu, oldval=oldval)

        if not islist(valu):
            self._raiseBadValu(valu, mesg='Expected guid or list/tuple')

        return self._norm_list(valu)

class XrefType(DataType):
    '''
    The XrefType allows linking a specific type of node to an inspecific
    set of node forms.

    Example Sub Type:

        addType('foo:barrefs', subof='xref', source='bar,foo:bar')

    '''

    def __init__(self, tlib, name, **info):
        DataType.__init__(self, tlib, name, **info)
        self._sorc_type = None
        self._sorc_name = None

        sorc = info.get('source')

        if sorc is not None:
            parts = sorc.split(',')
            if len(parts) != 2:
                raise s_common.BadInfoValu(name='source', valu=sorc, mesg='expected source=<name>,<type>')

            self._sorc_name = parts[0]
            self._sorc_type = parts[1]

    def norm(self, valu, oldval=None):

        if s_compat.isstr(valu):
            return self._norm_str(valu, oldval=oldval)

        if not islist(valu):
            self._raiseBadValu(valu, mesg='Expected guid, psv, or list')

        return self._norm_list(valu, oldval=None)

    def _norm_str(self, text, oldval=None):

        if len(text) == 32 and text.find('|') == -1:
            return self.tlib.getTypeNorm('guid', text)

        # FIXME full logical / quoted split
        parts = text.split('|')
        return self._norm_list(parts)

    def _norm_list(self, valu, oldval=None):

        if len(valu) != 3:
            self._raiseBadValu(text, mesg='xref type requires 3 fields')

        valu, tstr, tval = valu

        valu, vsub = self.tlib.getTypeNorm(self._sorc_type, valu)
        tval, tsub = self.tlib.getTypeNorm(tstr, tval)

        iden = s_common.guid((valu, tstr, tval))

        subs = {
            self._sorc_name: valu,
            'xtype': tstr,
            'xref:%s' % tstr: tval,
        }

        return iden, subs

class TimeType(DataType):
    # FIXME subfields for various time parts (year,month,etc)

    def __init__(self, tlib, name, **info):
        DataType.__init__(self, tlib, name, **info)

        self.ismin = info.get('ismin', False)
        self.ismax = info.get('ismax', False)

        self.minmax = None

        if self.ismin:
            self.minmax = min

        elif self.ismax:
            self.minmax = max

    def norm(self, valu, oldval=None):

        subs = {}

        # make the string into int form then apply our min/max
        if s_compat.isstr(valu):
            valu, subs = self._norm_str(valu, oldval=oldval)

        if oldval is not None and self.minmax:
            valu = self.minmax(valu, oldval)

        return valu, subs

    def _norm_str(self, text, oldval=None):
        return s_time.parse(text), {}

    def repr(self, valu):
        return s_time.repr(valu)

class SeprType(MultiFieldType):

    def __init__(self, tlib, name, **info):
        MultiFieldType.__init__(self, tlib, name, **info)
        self.sepr = info.get('sep', ',')
        self.reverse = info.get('reverse', 0)

    def norm(self, valu, oldval=None):
        subs = {}
        reprs = []

        if s_compat.isstr(valu):
            valu = self._split_str(valu)

        # only other possiblity should be that it was a list
        for part, (name, tobj) in self._zipvals(valu):

            if tobj == self:
                norm, nsub = part, {}
                reprs.append(norm)
            else:
                norm, nsub = tobj.norm(part)
                reprs.append(tobj.repr(norm))

            subs[name] = norm
            for subn, subv in nsub.items():
                subs['%s:%s' % (name, subn)] = subv

        return self.sepr.join(reprs), subs

    def _split_str(self, text):

        fields = self._get_fields()

        if self.reverse:
            parts = text.rsplit(self.sepr, len(fields) - 1)
        else:
            parts = text.split(self.sepr, len(fields) - 1)

        if len(parts) != len(fields):
            self._raiseBadValu(text, sep=self.sepr, mesg='split: %d fields: %d' % (len(parts), len(fields)))

        return parts

    def _zipvals(self, vals):
        return s_compat.iterzip(vals, self._get_fields())

class BoolType(DataType):

    def norm(self, valu, oldval=None):
        if s_compat.isstr(valu):
            valu = valu.lower()
            if valu in ('true', 't', 'y', 'yes', '1', 'on'):
                return 1, {}

            if valu in ('false', 'f', 'n', 'no', '0', 'off'):
                return 0, {}

            self._raiseBadValu(valu, mesg='Invalid boolean string')

        return int(bool(valu)), {}

    def repr(self, valu):
        return repr(bool(valu))

tagre = re.compile(r'^([\w]+\.)*[\w]+$')
class TagType(DataType):

    def norm(self, valu, oldval=None):

        parts = valu.split('@', 1)

        subs = {}

        if len(parts) == 2:

            strs = parts[1].split('-')
            tims = [self.tlib.getTypeNorm('time', s)[0] for s in strs]

            tmin = min(tims)
            tmax = max(tims)

            subs['seen:min'] = tmin
            subs['seen:max'] = tmax

        retn = parts[0].lower()
        if not tagre.match(retn):
            self._raiseBadValu(valu)

        return retn, subs

class TypeLib:
    '''
    An extensible type library for use in cortex data models.
    '''
    def __init__(self, load=True):
        self.types = {}
        self.casts = {}
        self.typeinfo = {}
        self.typetree = {}
        self.subscache = {}

        # pend creation of subtypes for non-existant base types
        # until the base type gets loaded.
        self.pended = collections.defaultdict(list)

        self.addType('str', ctor='synapse.lib.types.StrType', doc='The base string type')
        self.addType('int', ctor='synapse.lib.types.IntType', doc='The base integer type')
        self.addType('bool', ctor='synapse.lib.types.BoolType', doc='A boolean type')
        self.addType('json', ctor='synapse.lib.types.JsonType', doc='A json type (stored as str)')

        self.addType('guid', ctor='synapse.lib.types.GuidType', doc='A Globally Unique Identifier type')
        self.addType('sepr', ctor='synapse.lib.types.SeprType',
                     doc='A multi-field composite type which uses separated repr values')
        self.addType('comp', ctor='synapse.lib.types.CompType',
                     doc='A multi-field composite type which generates a stable guid from normalized fields')
        self.addType('xref', ctor='synapse.lib.types.XrefType',
                     doc='A multi-field composite type which can be used to link a known form to an unknown form')
        self.addType('time', ctor='synapse.lib.types.TimeType',
                     doc='Timestamp in milliseconds since epoch', ex='20161216084632')

        self.addType('syn:tag', ctor='synapse.lib.types.TagType', doc='A synapse tag', ex='foo.bar')

        # add base synapse types
        self.addType('syn:prop', subof='str', regex=r'^([\w]+:)*([\w]+|\*)$', lower=1)
        self.addType('syn:type', subof='str', regex=r'^([\w]+:)*[\w]+$', lower=1)
        self.addType('syn:glob', subof='str', regex=r'^([\w]+:)*[\w]+:\*$', lower=1)

        self.addType('int:min', subof='int', ismin=1)
        self.addType('int:max', subof='int', ismax=1)

        self.addType('str:lwr', subof='str', lower=1, strip=1)
        self.addType('str:txt', subof='str', doc='Multi-line text or text blob.')
        self.addType('str:hex', subof='str', frob_int_fmt='%x', regex=r'^[0-9a-f]+$', lower=1)

        self.addTypeCast('country:2:cc', self._castCountry2CC)
        self.addTypeCast('make:guid', self._castMakeGuid)

        if load:
            self.loadModModels()

    def _castCountry2CC(self, valu):
        valu = valu.replace('.', '').lower()
        return s_l_iso3166.country2iso.get(valu)

    def _castMakeGuid(self, valu):
        return s_common.guid(valu)

    def getTypeInst(self, name):
        '''
        Return the DataType instance for the given type name.

        Example:

            dtype = tlib.getTypeInst('foo:bar')

        NOTE: This API returns non-primitive objects and can not be
              used over telepath RMI.
        '''
        return self.types.get(name)

    def getTypeBases(self, name):
        '''
        Return a list of type inheritence names beginning with the base type.

        Example:

            for base in tlib.getTypeBases('foo:minval'):
                print('base type: %s' % (name,))

        '''
        done = [name]

        todo = self.typetree.get(name)
        while todo is not None:
            done.append(todo)
            todo = self.typetree.get(todo)

        done.reverse()
        return done

    def isSubType(self, name, base):
        '''
        Returns True if the given type name is a sub-type of the base name.

        Example:

            if tlib.isSubType('foo','str'):
                dostuff()

        '''
        key = (name, base)

        ret = self.subscache.get(key)
        if ret is None:
            ret = base in self.getTypeBases(name)
            self.subscache[key] = ret

        return ret

    def loadDataModels(self, modtups):
        '''
        Load a list of (name,model) tuples into the TypeLib.
        '''
        subtodo = []

        for modname, moddict in modtups:
            # add all base types first to simplify deps
            for name, info in moddict.get('types', ()):
                try:
                    self.addType(name, **info)
                except Exception as e:
                    logger.exception('type %s: %s' % (name, e))

    def loadModModels(self):

        dynmodls = s_modules.call_ctor('getBaseModels')

        models = [(modname, modl) for name, modls, excp in dynmodls for modname, modl in modls if modls]

        self.loadDataModels(models)

    def _bumpBasePend(self, name):
        for name, info in self.pended.pop(name, ()):
            try:
                self.addType(name, **info)
            except Exception as e:
                logger.exception('pended: addType %s' % (name,), e)

    def getDataType(self, name):
        '''
        Return the DataType subclass for the given type name.
        '''
        return self.types.get(name)

    def isDataType(self, name):
        '''
        Return boolean which is true if the given name is a data type.

        Example:

            if tlib.isDataType('foo:bar'):
                dostuff()

        '''
        return self.types.get(name) is not None

    def reqDataType(self, name):
        '''
        Return a reference to the named DataType or raise NoSuchType.
        '''
        item = self.getDataType(name)
        if item is None:
            raise s_common.NoSuchType(name=name)
        return item

    def addType(self, name, **info):
        '''
        Add a type to the cached types.

        Args:
            name (str): Name of the type to add.
            **info (dict): Type properties to include.

        Example:
            Add a new foo:bar type::

                tlib.addType('foo:bar', subof='str', doc='A foo bar.')

        Raises:
            DupTypeName: If the type already exists.

        '''
        if self.types.get(name) is not None:
            raise s_common.DupTypeName(name=name)

        ctor = info.get('ctor')
        subof = info.get('subof')
        if ctor is None and subof is None:
            raise Exception('addType must have either ctor= or subof=')

        if ctor is not None:
            self.typeinfo[name] = info

            try:
                item = s_dyndeps.tryDynFunc(ctor, self, name, **info)
                self.types[name] = item
                self._bumpBasePend(name)
                return True

            except Exception as e:
                logger.warning('failed to ctor type %s', name, exc_info=True)
                logger.debug('failed to ctor type %s', name, exc_info=True)
                self.typeinfo.pop(name, None)
        try:

            base = self.reqDataType(subof)
            # inherit docs and examples from parent types
            self.typeinfo[name] = info
            item = base.extend(name, **info)

            self.types[name] = item

            self._bumpBasePend(name)
            self.typetree[name] = subof
            self.subscache.clear()
            return True

        except s_common.NoSuchType as e:
            tnam = e.errinfo.get('name')
            self.typeinfo.pop(name, None)
            self.pended[tnam].append((name, info))
            return False

    def getTypeInfo(self, name, prop, defval=None):
        '''
        A helper to return an info prop for the type or it's parents.

        Example:

            ex = tlib.getTypeInfo('inet:tcp4','ex')

        '''
        todo = name
        while todo is not None:

            info = self.typeinfo.get(todo)
            if info is None:
                return defval

            ret = info.get(prop)
            if ret is not None:
                return ret

            todo = info.get('subof')

        return defval

    def getTypeNorm(self, name, valu, oldval=None):
        '''
        Normalize a type specific value in system mode.

        Example:

            fqdn,subs = tlib.getTypeNorm('inet:fqdn','Foo.Com')

        '''
        return self.reqDataType(name).norm(valu, oldval=oldval)

    def getTypeCast(self, name, valu):
        '''
        Use either a type or a registered "cast" name to normalize
        the given input.

        Example:

            valu = tlib.getTypeCast("foo:bar","hehe")

        '''
        func = self.casts.get(name)
        if func is not None:
            return func(valu)

        return self.getTypeNorm(name, valu)[0]

    def addTypeCast(self, name, func):
        '''
        Add a "cast" function to do normalization without
        creating a complete type.
        '''
        self.casts[name] = func

    def getTypeRepr(self, name, valu):
        '''
        Return the humon readable form of the given type value.

        Example:

            print( tlib.getTypeRepr('inet:ipv4', ipv4addr) )

        '''
        return self.reqDataType(name).repr(valu)

    def getTypeParse(self, name, text):
        '''
        Parse input text for the given type into it's system form.

        Example:

            ipv4,subs = tlib.getTypeParse('inet:ipv4','1.2.3.4')

        '''
        return self.reqDataType(name).parse(text)
