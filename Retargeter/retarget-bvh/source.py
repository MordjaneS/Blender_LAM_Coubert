# ------------------------------------------------------------------------------
#   BSD 2-Clause License
#   
#   Copyright (c) 2019-2020, Thomas Larsson
#   All rights reserved.
#   
#   Redistribution and use in source and binary forms, with or without
#   modification, are permitted provided that the following conditions are met:
#   
#   1. Redistributions of source code must retain the above copyright notice, this
#      list of conditions and the following disclaimer.
#   
#   2. Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#   
#   THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#   AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#   IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#   DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
#   FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
#   DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
#   SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
#   CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#   OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#   OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# ------------------------------------------------------------------------------

import bpy
import os
from collections import OrderedDict
from math import pi
from mathutils import *
from bpy.props import *

from .armature import CArmature
from .utils import *

#----------------------------------------------------------
#   Source classes
#----------------------------------------------------------
          
class CRigInfo:
    def __init__(self, scn, name="Automatic"):
        self.name = name
        self.filepath = "None"
        self.bones = []
        self.boneNames = {}
        self.parents = {}
        self.optional = []
        self.fingerprint = []
        self.t_pose = {}
        self.t_pose_file = None
        self.verbose = scn.McpVerbose


    def readFile(self, filepath):
        import json
        if self.verbose:
            print(self.verboseString, filepath)
        self.filepath = filepath
        with open(filepath, "r") as fp:
            struct = json.load(fp)
        if "name" in struct.keys():
            self.name = struct["name"]
        else:
            self.name = os.path.splitext(os.path.basename(filepath))[0]
        if "bones" in struct.keys():
            self.bones = [(key, nameOrNone(value)) for key,value in struct["bones"].items()]
            self.boneNames = dict([(canonicalName(key), value) for key,value in self.bones])
        if "parents" in struct.keys():
            self.parents = struct["parents"]
        if "optional" in struct.keys():
            self.optional = struct["optional"]
        if "fingerprint" in struct.keys():
            self.fingerprint = struct["fingerprint"]
        if "t-pose" in struct.keys():
            self.t_pose = struct["t-pose"]
        if "t-pose-file" in struct.keys():
            self.t_pose_file = struct["t-pose-file"]
 
 
    def addAutoBones(self, rig):
        self.bones = []
        for pb in rig.pose.bones:
            if pb.McpBone:
                self.bones.append( (pb.name, pb.McpBone) )
        self.addParents(rig)
        rig.McpTPoseDefined = False
        

    def addManualBones(self, rig):
        for pb in rig.pose.bones:
            pb.McpBone = ""
        for bname,mhx in self.bones:
            if bname in rig.pose.bones.keys():
                pb = rig.pose.bones[bname]
                pb.McpBone = mhx
            else:
                print("  ", bname)
        rig.McpTPoseDefined = False
        self.addParents(rig)


    def addTPose(self, rig):   
        for bname in self.t_pose.keys():
            if bname in rig.pose.bones.keys():
                pb = rig.pose.bones[bname]
                euler = Euler(Vector(self.t_pose[bname])*D)
                pb.McpQuat = euler.to_quaternion()
        rig.McpTPoseDefined = True


    def addParents(self, rig):        
        for pb in rig.pose.bones:
            if pb.McpBone:
                pb.McpParent = ""
                par = pb.parent
                while par:
                    if par.McpBone:
                        pb.McpParent = par.name
                        break
                    par = par.parent
        for bname,pname in self.parents.items():
            if bname in rig.pose.bones.keys():
                pb = rig.pose.bones[bname]
                pb.McpParent = pname
    
        if self.verbose:
            print("Parents")
            for pb in rig.pose.bones:
                if pb.McpBone:
                    print("  ", pb.name, pb.McpParent)
                       

    def testRig(self, name, rig, scn):
        from .armature import validBone
        print("Testing %s" % name)
        pbones = dict([(pb.name,pb) for pb in rig.pose.bones])
        for pb in rig.pose.bones:
            pbones[pb.name.lower()] = pb
        for (bname, mhxname) in self.bones:
            if bname in self.optional:
                continue
            if bname[0:2] == "f_" and not scn.McpIncludeFingers:
                continue
            if bname in pbones.keys():
                pb = pbones[bname]
            else:
                pb = None
            if pb is None or not validBone(pb):
                print("  Did not find bone %s (%s)" % (bname, mhxname))
                print("Bones:")
                for pair in self.bones:
                    print("  %s : %s" % pair)
                raise MocapError(
                    "Armature %s does not\n" % rig.name +
                    "match armature %s.\n" % name +
                    "Did not find bone %s     " % bname)


class CSourceInfo(CArmature, CRigInfo):
    verboseString = "Read source file"
    
    def __init__(self, scn, struct=None):
        CArmature.__init__(self, scn)
        CRigInfo.__init__(self, scn)
                    
#----------------------------------------------------------
#   Global variables
#----------------------------------------------------------

_sourceInfos = {}
_activeSrcInfo = None

def getSourceArmature(name):
    global _sourceInfos
    return _sourceInfos[name]

def getSourceBoneName(bname):
    global _activeSrcInfo
    lname = canonicalName(bname)
    try:
        return _activeSrcInfo.boneNames[lname]
    except KeyError:
        return None

def isSourceInited(scn):
    global _sourceInfos
    return (_sourceInfos != {})

def ensureSourceInited(scn):
    if not isSourceInited(scn):
        initSources(scn)

#
#   findSourceArmature(context, rig, auto):
#

def findSourceArmature(context, rig, auto):
    global _activeSrcInfo, _sourceInfos
    from .t_pose import autoTPose, putInRestPose, getTPoseInfo, putInRightPose
    scn = context.scene

    ensureSourceInited(scn)
    if auto:
        from .target import guessArmatureFromList
        scn.McpSourceRig, scn.McpSourceTPose = guessArmatureFromList(rig, scn, _sourceInfos) 
    
    if scn.McpSourceRig == "Automatic":
        info = CSourceInfo(scn)
        tposed = putInRightPose(rig, scn.McpSourceTPose, context)
        info.findArmature(rig)
        info.addAutoBones(rig)
        if not tposed:
            autoTPose(rig, context)
            scn.McpSourceTPose = "Default"
        _activeSrcInfo = _sourceInfos["Automatic"] = info
        info.display("Source")
    else:
        info = _activeSrcInfo = _sourceInfos[scn.McpSourceRig]
        info.addManualBones(rig)
        tinfo = getTPoseInfo(scn.McpSourceTPose)
        if tinfo:
            tinfo.addTPose(rig)
        else:
            scn.McpSourceTPose = "Default"

    rig.McpArmature = _activeSrcInfo.name
    print("Using source armature %s." % rig.McpArmature)

#
#    setSourceArmature(rig, scn)
#

def setSourceArmature(rig, scn):
    global _activeSrcInfo, _sourceInfos
    name = rig.McpArmature
    if name:
        scn.McpSourceRig = name
    else:
        raise MocapError("No source armature set")
    _activeSrcInfo = _sourceInfos[name]
    print("Set source armature to %s" % name)
    

#----------------------------------------------------------
#   Class
#----------------------------------------------------------

class Source:
    useAutoSource : BoolProperty(
        name = "Auto Source",
        description = "Find source rig automatically",
        default = True)
        
    def draw(self, context):
        self.layout.prop(self, "useAutoSource")
        if not self.useAutoSource:
            scn = context.scene
            self.layout.prop(scn, "McpSourceRig")
            self.layout.prop(scn, "McpSourceTPose")
        self.layout.separator()

    def findSource(self, context, rig):
        return findSourceArmature(context, rig, self.useAutoSource)

#----------------------------------------------------------
#   Source initialization
#----------------------------------------------------------

class MCP_OT_InitSources(bpy.types.Operator):
    bl_idname = "mcp.init_sources"
    bl_label = "Init Source Panel"
    bl_description = "(Re)load all json files in the source_rigs directory."
    bl_options = {'UNDO'}

    def execute(self, context):
        initSources(context.scene)
        return{'FINISHED'}


def initSources(scn):
    from .t_pose import initTPoses    
    initTPoses(scn)

    global _sourceInfos
    _sourceInfos = { "Automatic" : CSourceInfo(scn) }
    folder = os.path.join(os.path.dirname(__file__), "source_rigs")
    keys = []
    for fname in os.listdir(folder):
        filepath = os.path.join(folder, fname)
        if os.path.splitext(fname)[-1] == ".json":
            info = CSourceInfo(scn)
            info.readFile(filepath)            
            _sourceInfos[info.name] = info
            keys.append(info.name)
    enums = []
    keys.sort()
    keys = ["Automatic"] + keys
    for key in keys:
        enums.append((key,key,key))

    bpy.types.Scene.McpSourceRig = EnumProperty(
        items = enums,
        name = "Source rig",
        default = 'Automatic')
    scn.McpSourceRig = 'Automatic'
    print("Defined McpSourceRig")


#----------------------------------------------------------
#   List Rig
#
#   (mhx bone, text)
#----------------------------------------------------------

ListedBones = [
    ('hips',         'Root bone'),
    ('spine',        'Lower spine'),
    ('spine-1',      'Lower spine 2'),
    ('chest',        'Upper spine'),
    ('chest-1',      'Upper spine 2'),
    ('neck',         'Neck'),
    ('head',         'Head'),
    None,
    ('shoulder.L',   'L shoulder'),
    ('upper_arm.L',  'L upper arm'),
    ('forearm.L',    'L forearm'),
    ('hand.L',       'L hand'),
    None,
    ('shoulder.R',   'R shoulder'),
    ('upper_arm.R',  'R upper arm'),
    ('forearm.R',    'R forearm'),
    ('hand.R',       'R hand'),
    None,
    ('hip.L',        'L hip'),
    ('thigh.L',      'L thigh'),
    ('shin.L',       'L shin'),
    ('foot.L',       'L foot'),
    ('toe.L',        'L toes'),
    None,
    ('hip.R',        'R hip'),
    ('thigh.R',      'R thigh'),
    ('shin.R',       'R shin'),
    ('foot.R',       'R foot'),
    ('toe.R',        'R toes'),
    None,
    ("f_thumb.01.L",   "L thumb 1"),
    ("f_thumb.02.L",   "L thumb 2"),
    ("f_thumb.03.L",   "L thumb 3"),
    ("f_index.01.L",   "L index 1"),
    ("f_index.02.L",   "L index 2"),
    ("f_index.03.L",   "L index 3"),
    ("f_middle.01.L",   "L middle 1"),
    ("f_middle.02.L",   "L middle 2"),
    ("f_middle.03.L",   "L middle 3"),
    ("f_ring.01.L",   "L ring 1"),
    ("f_ring.02.L",   "L ring 2"),
    ("f_ring.03.L",   "L ring 3"),
    ("f_pinky.01.L",   "L pinky 1"),
    ("f_pinky.02.L",   "L pinky 2"),
    ("f_pinky.03.L",   "L pinky 3"),
    None,
    ("f_thumb.01.R",   "R thumb 1"),
    ("f_thumb.02.R",   "R thumb 2"),
    ("f_thumb.03.R",   "R thumb 3"),
    ("f_index.01.R",   "R index 1"),
    ("f_index.02.R",   "R index 2"),
    ("f_index.03.R",   "R index 3"),
    ("f_middle.01.R",   "R middle 1"),
    ("f_middle.02.R",   "R middle 2"),
    ("f_middle.03.R",   "R middle 3"),
    ("f_ring.01.R",   "R ring 1"),
    ("f_ring.02.R",   "R ring 2"),
    ("f_ring.03.R",   "R ring 3"),
    ("f_pinky.01.R",   "R pinky 1"),
    ("f_pinky.02.R",   "R pinky 2"),
    ("f_pinky.03.R",   "R pinky 3"),
]

class ListRig:
    def draw(self, context):
        bones, tpose = self.getBones(context)
        if bones:
            box = self.layout.box()
            for boneText in ListedBones:
                if not boneText:
                    box.separator()
                    continue
                (mhx, text) = boneText
                bnames = self.findKeys(mhx, bones)
                if bnames:
                    for bname in bnames:
                        row = box.row()
                        row.label(text=text)
                        row.label(text=bname)
                        if bname in tpose.keys():
                            row.label(text=str(tpose[bname]))
                        else:
                            row.label(text="")
                else:
                    row = box.row()
                    row.label(text=text)
                    row.label(text="-")
                    row.label(text="")


    def findKeys(self, mhx, bones):
        for bone,mhx1 in bones:
            if mhx == mhx1:
                return [bone]
        return []


class MCP_OT_ListSourceRig(BvhPropsOperator, ListRig):
    bl_idname = "mcp.list_source_rig"
    bl_label = "List Source Rig"
    bl_description = "List the bone associations of the active source rig"
    bl_options = {'UNDO'}

    @classmethod
    def poll(self, context):
        return context.scene.McpSourceRig

    def getBones(self, context): 
        from .t_pose import getTPoseInfo
        scn = context.scene 
        info = getSourceArmature(scn.McpSourceRig)
        tinfo = getTPoseInfo(scn.McpSourceTPose)
        if info and tinfo:
            return info.bones, tinfo.t_pose
        elif info:
            return info.bones, {}
        else:
            return [], {}


class MCP_OT_VerifySourceRig(BvhOperator):
    bl_idname = "mcp.verify_source_rig"
    bl_label = "Verify Source Rig"
    bl_description = "Verify the source rig type of the active armature"
    bl_options = {'UNDO'}
        
    @classmethod
    def poll(self, context):
        ob = context.object
        return (context.scene.McpSourceRig and ob and ob.type == 'ARMATURE')
                
    def run(self, context):   
        rigtype = context.scene.McpSourceRig     
        info = _sourceInfos[rigtype]
        info.testRig(rigtype, context.object, context.scene)
        raise MocapMessage("Source armature %s verified" % rigtype)


class MCP_OT_IdentifySourceRig(BvhOperator):
    bl_idname = "mcp.identify_source_rig"
    bl_label = "Identify Source Rig"
    bl_description = "Identify the source rig type of the active armature"
    bl_options = {'UNDO'}
        
    @classmethod
    def poll(self, context):
        ob = context.object
        return (ob and ob.type == 'ARMATURE')

    def run(self, context):   
        from .target import guessArmatureFromList
        from .t_pose import getTPoseInfo
        scn = context.scene
        rig = context.object
        scn.McpSourceRig,scn.McpSourceTPose = guessArmatureFromList(rig, scn, _sourceInfos)  
        info = _sourceInfos[scn.McpSourceRig]
        if scn.McpSourceRig == "Automatic":
            info.addAutoBones(rig)
        else:
            info.addManualBones(rig)
            tinfo = getTPoseInfo(info.t_pose_file)
            if tinfo:
                scn.McpSourceTPose = tinfo.name
                tinfo.addTPose(rig)
        print("Identified rig %s" % scn.McpSourceRig)
                      
#----------------------------------------------------------
#   Initialize
#----------------------------------------------------------

classes = [
    MCP_OT_InitSources,
    MCP_OT_ListSourceRig,
    MCP_OT_VerifySourceRig,
    MCP_OT_IdentifySourceRig,
]

def initialize():
    bpy.types.Scene.McpSourceRig = EnumProperty(
        items = [("Automatic", "Automatic", "Automatic")],
        name = "Source Rig",
        default = "Automatic")  
        
    bpy.types.Scene.McpSourceTPose = EnumProperty(
        items = [("Default", "Default", "Default")],
        name = "TPose Source",
        default = "Default")              

    bpy.types.Object.McpArmature = StringProperty()

    for cls in classes:
        bpy.utils.register_class(cls)


def uninitialize():
    for cls in classes:
        bpy.utils.unregister_class(cls)
