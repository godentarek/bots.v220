import re
import zipfile
from django.utils.translation import ugettext as _
#bots-modules
import botslib
import botsglobal
from botsconfig import *

@botslib.log_session
def preprocess(routedict,function, status=FILEIN,**argv):
    ''' for pre- and postprocessing of files.
        these are NOT translations; translation involve grammers, mapping scripts etc. think of eg:
        - unzipping zipped files.
        - convert excel to csv
        - password protected files.
        Select files from INFILE -> SET_FOR_PROCESSING using criteria
        Than the actual processing function is called.
        The processing function does: SET_FOR_PROCESSING -> PROCESSING -> FILEIN
        If errors occur during processing, no ta are left with status FILEIN !
        preprocess is called right after the in-communicatiation
    '''
    nr_files = 0
    preprocessnumber = botslib.getpreprocessnumber()
    if not botslib.addinfo(change={'status':preprocessnumber},where={'status':status,'idroute':routedict['idroute'],'fromchannel':routedict['fromchannel']}):    #check if there is something to do
        return nr_files
    for row in botslib.query(u'''SELECT idta,filename,charset
                                FROM  ta
                                WHERE   idta>%(rootidta)s
                                AND     status=%(status)s
                                AND     statust=%(statust)s
                                AND     idroute=%(idroute)s
                                AND     fromchannel=%(fromchannel)s
                                ''',
                                {'status':preprocessnumber,'statust':OK,'idroute':routedict['idroute'],'fromchannel':routedict['fromchannel'],'rootidta':botslib.get_minta4query()}):
        try:
            botsglobal.logmap.debug(u'Start preprocessing "%s" for file "%s".',function.__name__,row['filename'])
            ta_set_for_processing = botslib.OldTransaction(row['idta'])
            ta_processing = ta_set_for_processing.copyta(status=preprocessnumber+1)
            ta_processing.filename=row['filename']
            function(ta_from=ta_processing,endstatus=status,routedict=routedict,**argv)
        except:
            txt=botslib.txtexc()
            ta_processing.failure()
            ta_processing.update(statust=ERROR,errortext=txt)
        else:
            botsglobal.logmap.debug(u'OK preprocessing  "%s" for file "%s".',function.__name__,row['filename'])
            ta_set_for_processing.update(statust=DONE)
            #~ ta_processing.succes(INFILE)    #
            ta_processing.update(statust=DONE)
            nr_files += 1
    return nr_files




header = re.compile('(\s*(ISA))|(\s*(UNA.{6})?\s*(U\s*N\s*B)s*.{1}(.{4}).{1}(.{1}))',re.DOTALL)
#           group:    1   2       3  4            5        6         7

def mailbag(ta_from,endstatus,**argv):
    ''' splits 'mailbag'files to separate files each containing one interchange (ISA-IEA or UNA/UNB-UNZ).
        handles x12 and edifact; these can be mixed.
        recognizes xml files. messagetype 'xml' has a special handling when reading xml-files.

        about auto-detect/mailbag:
        - in US mailbag is used: one file for all received edi messages...appended in one file. I heard that edifact and x12 can be mixed,
            but have actually never seen this.
        - bots needs a 'splitter': one edi-file, more interchanges. it is preferred to split these first.
        - handle multiple UNA in one file, including different charsets.
        - auto-detect: is is x12, edifact, xml, or??
    '''
    edifile = botslib.readdata(filename=ta_from.filename)       #read as binary...
    startpos=0
    while (1):
        found = header.search(edifile[startpos:])
        if found is None:
            if startpos:    #ISA/UNB have been found in file; no new ISA/UNB is found. So all processing is done.
                break
            #guess if this is an xml file.....
            sniffxml = edifile[:25]
            sniffxml = sniffxml.lstrip(' \t\n\r\f\v\xFF\xFE\xEF\xBB\xBF\x00')       #to find first ' real' data; some char are because of BOM, UTF-16 etc
            if sniffxml and sniffxml[0]=='<':
                ta_to=ta_from.copyta(status=endstatus,statust=OK,filename=ta_from.filename,editype='xml',messagetype='xml')  #make transaction for translated message; gets ta_info of ta_frommes
                #~ ta_tomes.update(status=STATUSTMP,statust=OK,filename=ta_set_for_processing.filename,editype='xml') #update outmessage transaction with ta_info;
                break;
            else:
                raise botslib.InMessageError(_(u'Found no content in mailbag.'))
        elif found.group(1):
            editype='x12'
            headpos=startpos+ found.start(2)
            count=0
            for c in edifile[headpos:headpos+120]:  #search first 120 characters to find separators
                if c in '\r\n' and count!=105:
                    continue
                count +=1
                if count==4:
                    field_sep = c
                elif count==106:
                    record_sep = c
                    break
            #~ foundtrailer = re.search(re.escape(record_sep)+'\s*IEA'+re.escape(field_sep)+'.+?'+re.escape(record_sep),edifile[headpos:],re.DOTALL)
            foundtrailer = re.search(re.escape(record_sep)+'\s*I\s*E\s*A\s*'+re.escape(field_sep)+'.+?'+re.escape(record_sep),edifile[headpos:],re.DOTALL)
        elif found.group(3):
            editype='edifact'
            if found.group(4):
                field_sep = edifile[startpos + found.start(4) + 4]
                record_sep = edifile[startpos + found.start(4) + 8]
                headpos=startpos+ found.start(4)
            else:
                field_sep = '+'
                record_sep = "'"
                headpos=startpos+ found.start(5)
            foundtrailer = re.search(re.escape(record_sep)+'\s*U\s*N\s*Z\s*'+re.escape(field_sep)+'.+?'+re.escape(record_sep),edifile[headpos:],re.DOTALL)
        if not foundtrailer:
            raise botslib.InMessageError(_(u'Found no valid envelope trailer in mailbag.'))
        endpos = headpos+foundtrailer.end()
        #so: interchange is from headerpos untill endpos
        #~ if header.search(edifile[headpos+25:endpos]):   #check if there is another header in the interchange
            #~ raise botslib.InMessageError(u'Error in mailbag format: found no valid envelope trailer.')
        ta_to = ta_from.copyta(status=endstatus)  #make transaction for translated message; gets ta_info of ta_frommes
        tofilename = str(ta_to.idta)
        tofile = botslib.opendata(tofilename,'wb')
        tofile.write(edifile[headpos:endpos])
        tofile.close()
        ta_to.update(statust=OK,filename=tofilename,editype=editype,messagetype=editype) #update outmessage transaction with ta_info;
        startpos=endpos
        botsglobal.logger.debug(_(u'        File written: "%s".'),tofilename)

def botsunzip(ta_from,endstatus,password=None,pass_non_zip=False,**argv):
    ''' unzip file; editype & messagetype are unchanged.
        if not a zipfile: save as editype mailbag, messagetype mailbag.
    '''
    try:
        z = zipfile.ZipFile(botslib.abspathdata(filename=ta_from.filename),mode='r')
    except zipfile.BadZipfile:
        botsglobal.logger.debug(_(u'File is not a zip-file.'))
        if pass_non_zip:        #just pass the file
            botsglobal.logger.debug(_(u'"pass_non_zip" is True, just pass the file.'))
            ta_to = ta_from.copyta(status=endstatus,statust=OK)
            return
        raise botslib.InMessageError(_(u'File is not a zip-file.'))
        
    if password:
        z.setpassword(password)
    for f in z.infolist():
        if f.filename[-1] == '/':    #check if this is a dir; if so continue
            continue
        ta_to = ta_from.copyta(status=endstatus)
        tofilename = str(ta_to.idta)
        tofile = botslib.opendata(tofilename,'wb')
        tofile.write(z.read(f.filename))
        tofile.close()
        ta_to.update(statust=OK,filename=tofilename) #update outmessage transaction with ta_info; 
        botsglobal.logger.debug(_(u'        File written: "%s".'),tofilename)