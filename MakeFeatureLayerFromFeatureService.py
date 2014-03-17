#!/usr/bin/python
# -*- coding: utf-8 -*-
#
import os, traceback
import sys
import arcpy
import arcpy.mapping as mapping
import urllib, urllib2, json
import errno

#for the application data folder
import ctypes
from ctypes import wintypes, windll

def make_sure_dir_exists(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise
        
#from http://stackoverflow.com/questions/626796/how-do-i-find-the-windows-common-application-data-folder-using-python
#
def AppDataFolder():
    CSIDL_COMMON_APPDATA = 35

    _SHGetFolderPath = windll.shell32.SHGetFolderPathW
    _SHGetFolderPath.argtypes = [wintypes.HWND,
                                ctypes.c_int,
                                wintypes.HANDLE,
                                wintypes.DWORD, wintypes.LPCWSTR]

    path_buf = wintypes.create_unicode_buffer(wintypes.MAX_PATH)
    folder = ""
    try:
        folder = _SHGetFolderPath(0, CSIDL_COMMON_APPDATA, 0, 0, path_buf)
    except:
        #fallback
        folder = os.path.expanduser("~")
    return folder

def scratchWorkspace():
    make_sure_dir_exists(arcpy.env.scratchFolder)
    if (not os.path.exists(os.path.join(arcpy.env.scratchFolder,"scratch.gdb"))):
        arcpy.CreateFileGDB_management(arcpy.env.scratchFolder,"scratch.gdb")
    return os.path.join(arcpy.env.scratchFolder,"scratch.gdb")
    
def AddMessageToGP(message="",is_error=False):
    if (is_error):
        arcpy.AddError(message)
    else:
        arcpy.AddMessage(message)
    
def FormatException(include_arcpy_errors=True):
    exc_type, exc_value, exc_traceback = sys.exc_info()
    tbmsg = repr(traceback.format_exception(exc_type, exc_value,
                                          exc_traceback)).replace(", in ",",\n in ")
    arcmsg = ""
    if (include_arcpy_errors):
        arcmsg = "ARCPY errors: " + arcpy.GetMessages(2)
        tbmsg += "\n" + arcmsg
        
    #pprint(traceback.format_exception(exc_type, exc_value, exc_traceback))
    return tbmsg

#refer to http://atlas.resources.ca.gov/arcgis/sdk/rest/index.html. Click on "Geometry Objects" for the
# JSON for Points, Lines, Polys
#
#refer to http://help.arcgis.com/en/arcgisdesktop/10.0/help/index.html#/Point/000v000000mv000000/, search for "Classes"
# - it is within the 'Arcpy site package' folder. Within Classes are the spec for Point, Line, Poly classes
# in python
#

fsURL = arcpy.GetParameterAsText(0)
wkid = arcpy.GetParameterAsText(1)
workspace = arcpy.GetParameterAsText(2)

if (wkid is None or len(wkid) == 0 or wkid == "0"):
    wkid = 4326
else:
    try:
        wkid = int(wkid)
    except:
        wkid = 4326


if (workspace is None or len(workspace) == 0):
    workspace = scratchWorkspace()
    
fs_query1 = "{url}?f=json".format
fs_query2 = "{url}/query?where=1=1&outFields={listOfFields}&outSR={wkid}&f=json".format

requestURL1 = fs_query1(url=fsURL)


obj = None
fc = None
fl = None
fieldList = []
arcpy.env.overwriteOutput = True

try:
    opener = urllib2.build_opener()
    #metadata first
    req = urllib2.Request(requestURL1)
    
    AddMessageToGP("Querying feature service for metadata...")
    
    stm = opener.open(req)
    obj = json.loads(stm.read())
    
    #get the layer name
    layerName = obj["name"]
    
    #points lines or polys?
    geomType = obj["geometryType"]
    fc_geomType = ""
    if (geomType == "esriGeometryPoint"):
        fc_geomType = "POINT"
    elif (geomType == "esriGeometryMultipoint"):
        fc_geomType = "MULTIPOINT"
    elif (geomType == "esriGeometryPolyline"):
        fc_geomType = "POLYLINE"
    elif (geomType == "esriGeometryPolygon"):
        fc_geomType = "POLYGON"
    else:
        raise RuntimeError("Unsupported geometry type: {0}".format(geomType))
            
    AddMessageToGP("Creating temporary feature class...")
    #make a temporary feature class
    sr = arcpy.SpatialReference(wkid) #projection
    fc_name = os.path.basename(arcpy.CreateUniqueName(layerName + "_fc", workspace))
    fc = arcpy.CreateFeatureclass_management(workspace,
                                 fc_name,
                                 fc_geomType,
                                 spatial_reference=sr).getOutput(0)
    
    #add fields
    for field in obj["fields"]:
        ftype = ""
        if (field["type"] == "esriFieldTypeString"):
            arcpy.AddField_management(fc,field["name"],"TEXT",field_length=field["length"],field_alias=field["alias"])
            fieldList.append((field["name"],"TEXT"))
        elif (field["type"] == "esriFieldTypeSmallInteger" or
              field["type"] == "esriFieldTypeInteger"):
            arcpy.AddField_management(fc,field["name"],"LONG",field_alias=field["alias"])
            fieldList.append((field["name"],"LONG"))
        elif (field["type"] == "esriFieldTypeDouble" or 
              field["type"] == "esriFieldTypeSingle"):
            arcpy.AddField_management(fc,field["name"],"DOUBLE",field_alias=field["alias"])
            fieldList.append((field["name"],"DOUBLE"))
        elif (field["type"] == "esriFieldTypeDate"):
            arcpy.AddField_management(fc,field["name"],"DATE",field_alias=field["alias"])
            fieldList.append((field["name"],"DATE"))
        elif (field["type"] == "esriFieldTypeGlobalID" or field["type"] == "esriFieldTypeGUID"):
            arcpy.AddField_management(fc,field["name"],"TEXT",field_length=50,field_alias=field["alias"])
            fieldList.append((field["name"],"TEXT"))
    
    #now make the query url
    fldList = [flds[0] for flds in fieldList]
    fieldNames = ",".join(fldList)
    requestURL2 = fs_query2(url=fsURL,wkid=wkid,listOfFields=fieldNames)
    
    try:
        #now get the actual records
        req = urllib2.Request(requestURL2)
        
        AddMessageToGP("Querying feature service for features...")

        stm = opener.open(req)
        obj = json.loads(stm.read())
        
        #if we are here, the query returned successfully
        #
        #create an insert cursor to add our new rows
        ic = arcpy.InsertCursor(fc)
        count = 1
        
        AddMessageToGP("Inserting features...")
        
        for feature in obj["features"]:
            geometry = None
            #bad features can have null geometry
            if ("geometry" in feature):
                geometry = feature["geometry"]
            attributes = feature["attributes"]
            #new row
            row = ic.newRow()
            
            try:
                if (geometry is not None):
                    row.shape = arcpy.AsShape(geometry,True)

                #attributes
                for fldName in attributes.keys():
                    try:
                        row.setValue(fldName,attributes[fldName])
                    except:
                        pass
                ic.insertRow(row)
                
            except:
                msg = "Exception on feature {0:d}, {1}".format(count, FormatException())
                AddMessageToGP(msg,True)
                
            del row
            count += 1
                    
        AddMessageToGP("Processed {0:d} features".format(count))
        #make the featture layer
        try:
            AddMessageToGP("Creating feature layer {0}...".format(layerName))
            
            fl = arcpy.MakeFeatureLayer_management(fc,out_layer=layerName)
            
            AddMessageToGP("Created feature layer {0}".format(layerName))
        except:
            msg = "Error creating feature layer {0}, {1}".format(layerName, FormatException())
            AddMessageToGP(msg,True)
    except:
        AddMessageToGP(FormatException(),True)
except:
    AddMessageToGP(FormatException(),True)

if (fl is not None):
    arcpy.SetParameter(3, fl)
AddMessageToGP("Done")